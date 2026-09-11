"""Read-only evidence audit and reproducible UBS transaction-cost report."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.report import parse_report, parse_deal_audit, parse_entry_volume_audit
from mt5_ea_validator.transaction_cost import load_build_index
from mt5_ea_validator.ubs_report import STRATEGIES, WF_PERIODS, _svg_document
from mt5_ea_validator.ubs_smoke import load_ubs_smoke_settings
from mt5_ea_validator.ubs_transaction_cost import verified_zero, validate_positive_builds, zero_comparison

ROOT = Path('data/ubs_strategy_comparison/transaction_cost')
BASELINE = ROOT / 'source_refresh_20260910T131752446294_64e4e666/refreshed_baseline'
ZERO = ROOT / 'zero_suite_20260910T140315439336_9f3fe4e7'
RUN = ROOT / 'positive_suite_20260910T163425252024_18074a9c'
ZERO_INDEX = Path('data/transaction_cost_stress/symbol_builds/20260910T112654791817_3927cb61/build_index.json')
LEVELS = (0, 2, 5, 10)
LOTS = dict(zip(STRATEGIES, ('0.03','0.03','0.04','0.03','0.01','0.01','0.02')))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_grid(rows):
    expected = {(sid, wf, s) for sid in STRATEGIES for wf in WF_PERIODS for s in LEVELS}
    actual = {(r['strategy_id'], r['wf'], r['stress_points']) for r in rows}
    if len(rows) != 112 or actual != expected:
        raise ValueError('Expected 112 unique strategy/WF/stress cases')


def audit_row(r):
    path = Path(r['report_file'])
    if r['status'] != 'success' or r['mt5_return_code'] != 0:
        raise ValueError('Unsuccessful case')
    if parse_report(path).to_dict() != r['metrics'] or parse_deal_audit(path).to_dict() != r['deal_audit']:
        raise ValueError('HTML differs from recorded result')
    if sha256_file(path.parent / 'input_set.set') != r['set_sha256'] or r['set_sha256'] != r['selected_set_sha256']:
        raise ValueError('Input set changed')
    volume = parse_entry_volume_audit(path).to_dict()
    if r['metrics']['trades']:
        if any(Decimal(str(volume[k])) != Decimal(LOTS[r['strategy_id']])
               for k in ('minimum_entry_volume','maximum_entry_volume')):
            raise ValueError('Fixed lot differs')
    elif r['deal_audit']['deal_count'] or volume['entry_deal_count']:
        raise ValueError('Invalid empty case')
    q = r['tick_data_quality']
    if (not q['passed'] or not r['deal_symbol_audit']['passed']
            or (q['fallback_warning_count'] and q['fallback_minute_count'] is None)
            or (q['fallback_minute_count'] or 0) > 24):
        raise ValueError('Quality audit failed')


def load_rows(run=RUN):
    settings = load_ubs_smoke_settings(Path('config/ubs_gold_smoke.json'))
    zero, refs = verified_zero(settings, BASELINE, ZERO, ZERO_INDEX)
    manifest = read(run / 'run_manifest.json')
    index = Path(manifest['positive_index'])
    validate_positive_builds(zero, load_build_index(index))
    if (manifest['status'] != 'success' or len(manifest['results']) != 84
            or manifest['zero_manifest_sha256'] != sha256_file(ZERO / 'run_manifest.json')
            or manifest['positive_build_sha256'] != sha256_file(index)
            or manifest['ea_sha256'] != sha256_file(settings.expert_binary)):
        raise ValueError('Incomplete or changed campaign')
    rows = [dict(r, stress_points=0) for r in refs.values()]
    normal = {(r['wf'],r['strategy_id']):r for r in read(BASELINE / 'run_manifest.json')['results']}
    for r in rows:
        if not zero_comparison(r, normal[(r['wf'],r['strategy_id'])])['reproduction_passed']:
            raise ValueError('Zero reproduction failed')
    for record in manifest['results']:
        path = Path(record['result_file'])
        if sha256_file(path) != record['result_sha256']:
            raise ValueError('Result hash changed')
        r = read(path)
        if any(r[k] != v for k,v in record['case'].items()):
            raise ValueError('Case identity differs')
        ref = refs[(r['wf'],r['strategy_id'])]
        if r['selected_set_sha256'] != ref['selected_set_sha256']:
            raise ValueError('Stress set differs from S0')
        rows.append(r)
    validate_grid(rows)
    for r in rows:
        audit_row(r)
        r['delta'] = zero_comparison(r, refs[(r['wf'], r['strategy_id'])])
    rows.sort(key=lambda r:(STRATEGIES.index(r['strategy_id']), r['wf'], r['stress_points']))
    return rows


def select(rows, sid, s, wf=None):
    return [r for r in rows if r['strategy_id']==sid and r['stress_points']==s and (wf is None or r['wf']==wf)]


def summarize(rows):
    validate_grid(rows)
    summary = {}
    for sid in STRATEGIES:
        summary[sid] = {}
        for s in LEVELS:
            rr = select(rows,sid,s)
            summary[sid][s] = dict(np=round(sum(r['metrics']['net_profit'] for r in rr),2),
                dd=max(r['metrics']['equity_drawdown_percent'] for r in rr),
                trades=sum(r['metrics']['trades'] for r in rr),
                positive=sum(r['metrics']['net_profit']>0 for r in rr),
                same=sum(r['delta']['deal_sequence_same'] for r in rr),
                trade_changes=sum(r['delta']['trades_delta']!=0 for r in rr),
                commission=round(sum(r['deal_audit']['commission_total'] for r in rr),2),
                swap=round(sum(r['deal_audit']['swap_total'] for r in rr),2),
                gross=round(sum(r['deal_audit']['deal_profit_total'] for r in rr),2))
    return summary


def bars(summary, kind):
    levels = LEVELS[1:] if kind=='delta' else LEVELS
    title = {'np':'4WFの参考合計 Net Profit','delta':'S=0からの参考合計 Net Profit差','dd':'各水準の四半期Equity DD最大値'}[kind]
    subtitle = {'np':'各WFは3,000 USDから独立開始。年間連続テストではありません。',
                'delta':'差 = 加工後のNet Profit − S=0のNet Profit（4WF参考合計、USD）',
                'dd':'4WFのDDの最大値。DDの合計・年間連続DDではありません。'}[kind]
    def value(sid,s):
        return summary[sid][s]['np']-summary[sid][0]['np'] if kind=='delta' else summary[sid][s][kind]
    vals=[value(sid,s) for sid in STRATEGIES for s in levels]
    lo,hi=min(0,min(vals)),max(0,max(vals)); span=hi-lo or 1
    lo-=span*.05; hi+=span*.20
    x=lambda v:290+(v-lo)/(hi-lo)*780
    body=[f'<text class="title" x="30" y="42">{title}</text>',
          f'<text class="subtitle" x="30" y="78">{subtitle}</text>',
          '<text class="subtitle" x="30" y="112">' + ('青:S=0　' if kind!='delta' else '') + '緑:S=2　灰:S=5　紫:S=10</text>',
          '<style>.stress10{fill:#7c3aed}@media(prefers-color-scheme:dark){.stress10{fill:#c4b5fd}}</style>']
    for j in range(6):
        v=lo+(hi-lo)*j/5
        body += [f'<line class="grid" x1="{x(v):.1f}" y1="140" x2="{x(v):.1f}" y2="1070"/>',
                 f'<text class="axis-label" x="{x(v):.1f}" y="1100" text-anchor="middle">{v:.1f}</text>']
    body.append(f'<line class="axis" x1="{x(0):.1f}" y1="140" x2="{x(0):.1f}" y2="1070"/>')
    for i,sid in enumerate(STRATEGIES):
        y=158+i*130
        body.append(f'<text class="label" x="25" y="{y+46}">{sid}</text>')
        for j,s in enumerate(levels):
            v=value(sid,s); yy=y+j*28
            css={0:'ohlc',2:'real',5:'below-target',10:'stress10'}[s]
            body += [f'<rect class="{css}" x="{min(x(0),x(v)):.1f}" y="{yy}" width="{max(.7,abs(x(v)-x(0))):.1f}" height="19"/>',
                     f'<text class="value" x="{max(x(0),x(v))+8:.1f}" y="{yy+16}">{v:.2f}</text>']
    body.append(f'<text class="subtitle" x="30" y="1144">単位：{"%" if kind=="dd" else "USD"}　各戦略の採用固定ロットを維持。全戦略が同一リスクではありません。</text>')
    return _svg_document(title,subtitle,body,1175)


def heatmap(rows):
    body=['<text class="title" x="30" y="42">S=10のWF別 Net Profit差</text>',
          '<text class="subtitle" x="30" y="78">差 = S=10のNet Profit − S=0のNet Profit（USD）</text>',
          '<text class="subtitle" x="30" y="112">赤背景：減益／青背景：増益または不変。色は合否を意味しません。</text>']
    for j,wf in enumerate(WF_PERIODS):
        body.append(f'<text class="label" x="{390+j*210}" y="155" text-anchor="middle">{wf}</text>')
    for i,sid in enumerate(STRATEGIES):
        y=180+i*80
        body.append(f'<text class="label" x="25" y="{y+35}">{sid}</text>')
        for j,wf in enumerate(WF_PERIODS):
            v=select(rows,sid,10,wf)[0]['delta']['net_profit_delta']; xx=292+j*210
            body += [f'<rect class="{"negative-cell" if v<0 else "heat-1"}" x="{xx}" y="{y}" width="196" height="60" rx="4"/>',
                     f'<text class="value" x="{xx+98}" y="{y+37}" text-anchor="middle">{v:+.2f}</text>']
    return _svg_document('S=10のWF別損益差','7戦略・4WFのS=0からの差',body,760)


def build(output, run=RUN):
    assets=output.parent/'assets'/output.stem
    if output.exists() or assets.exists():
        raise FileExistsError('Existing report/assets are protected')
    rows=load_rows(run); summary=summarize(rows)
    lines=render_report(rows,summary,output,run)
    assets.mkdir(parents=True)
    for kind in ('np','delta','dd'):
        (assets/f'{kind}.svg').write_text(bars(summary,kind),encoding='utf-8')
    (assets/'wf-delta.svg').write_text(heatmap(rows),encoding='utf-8')
    output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary


def render_report(rows, summary, output, run):
    def table(headers, records):
        return ['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|'] + [
            '| '+' | '.join(str(v) for v in record)+' |' for record in records]
    def chart(name, alt):
        return f'![{alt}](assets/{output.stem}/{name}.svg)'
    lines=['# Ultimate Breakout System Gold 7戦略 — 取引コスト耐性（スリッページ関連・第1層）総合報告書','',
        '作成日：2026-09-11。対象：UBS v7.5 demo、ゴールド用7 set。比較112/112件完了。','',
        '## 1. 目的','', '### 1.1 これまでの検証との関係','',
        '[第1報告書](ubs_gold_strategy_comparison.md)は原setでの戦略比較、[第2報告書](ubs_same_risk_comparison.md)は固定ロットによるリスク調整、[第3報告書（7戦略版）](ubs_execution_delay_all_strategies.md)は約定遅延耐性を扱った。今回は第2報告書で採用したロットを維持し、売買価格の差（スプレッド）が広がった場合の損益・Equity DD・取引系列への影響を調べる。','',
        'ここでいう「第1層」はスリッページ関連の評価手法の区分であり、UBSの「第1報告書」を指す番号ではない。以前QQ・Smart Gold Hunter・Wave Riderで実施した第1層と同じ価格加工方式を使う。','',
        *table(['層','位置づけ','今回の扱い'],[
            ['第1層','Bid/Askを加工し、取引コストと価格判断への感応度をバックテストする','本報告書の対象'],
            ['第2層','EAの許容偏差設定と約定遅延などの組合せへの感応度を調べる','以前はQQのInpSlippageで実施。UBSで同じ機能を検証したという意味ではない'],
            ['第3層','フォワード稼働の要求価格・実約定価格・遅延等を記録して現実の執行を評価する','今回は未実施']]),'',
        '純粋な実約定スリッページの測定ではない。スリッページとは要求した価格と実際の約定価格との差であり、本試験はその不利な価格・コスト影響の一部を模擬する。価格データ自体を変えるため、EAの注文判断も変わり得る。','',
        '### 1.2 変更する量Sと価格加工','',
        '**Sは、BidとAskをそれぞれ外側へ動かす幅をpoints単位で表した数値**である。S=0が無加工の基準で、S=2・5・10を比較した。この銘柄のPointは0.01 USD/金価格単位である。','',
        '```text','Bid\' = Bid − S × Point','Ask\' = Ask ＋ S × Point',
        '加工後のスプレッド = 元のスプレッド ＋ 2 × S × Point','```','',
        'Bidは売れる価格、Askは買える価格、プライム（\'）は加工後を表す。買うときのAskを高く、売るときのBidを低くし、不利な価格で売買する条件を作る。固定手数料を損益から一括控除した計算とは異なる。','',
        *table(['S (points)','Bidの変更','Askの変更','スプレッド増分','7戦略×4WF'],[
            [s,f'−{s*.01:.2f}',f'+{s*.01:.2f}',f'{2*s} points / {2*s*.01:.2f} USD',28] for s in LEVELS]),'',
        '例：元Bid=2500.00、Ask=2500.15、S=5なら、Bid\'=2499.95、Ask\'=2500.20となり、スプレッドは0.15から0.25へ広がる。ここでのUSDは価格差で、口座の損失額そのものではない。損益への換算にはロット・契約サイズ・実際の注文/決済が関係する。','',
        '### 1.3 MT5でどこを変更したか','',
        '加工済みティックを入れたカスタムシンボルを用意し、ストラテジーテスターの「銘柄」で選択した。Sというテスター標準欄を変更したわけではない。銘柄内には加工後のBid\'・Ask\'を持つティックが収められている。M1のOHLCは−S×Point、spreadは+2S pointsとする既存方式も維持した。','',
        *table(['S','テスト銘柄'],[[0,'XAUUSD_TCS0_5a220d89'],[2,'XAUUSD_TCS2_6cc71094'],[5,'XAUUSD_TCS5_e479cd1e'],[10,'XAUUSD_TCS10_6cc71094']]),'',
        '遅延は全件No Delay（ExecutionMode=0）。今回、ランダム遅延や固定188msを組み合わせていない。','',
        '## 2. 結果','', '### 2.1 総合判断','',
        '- **eは今回の範囲で相対的に変化が小さい。** 4WF合計NPはS=0の420.31 USDに対し、S=2/5/10で418.03/423.07/418.18 USD。全16件が黒字で、四半期DD最大は3.73～3.76%。ただし年間リスク・将来収益の保証ではない。',
        '- **xau_h1_c5は合計利益首位を維持するが、S=10のリスク上昇に注意。** 合計NPは1299.51→1161.60 USD（−10.61%）。WF1はS=2から赤字、WF2のDDはS=10で8.42%となった。遅延耐性の良さだけでは価格コスト耐性を判断できない。',
        '- **daily_lは全16件黒字だが、減益感応度が大きく非単調。** S=5の合計NPは448.66 USD（S=0比−31.26%）。S=10は560.38 USDへ戻る一方、WF4は169.08→50.68 USDと大きく減少した。',
        '- xau_sr_scalp_h1は合計NPが430.47→396.90 USD、S=10のWF1が−8.97 USD。全水準・全期間で黒字という結果ではない。',
        '- goldtradepro_aはNP変化が小さいが、WF4のDDは13%台。最低lotでも高DDのため、これを「総合的に低リスク」と評価しない。',
        '- mt5_longterm_e・mt5_longterm_jは取引数も変化する。前者はS=5で減益しS=10で増益、後者はS増加に伴い合計NPが減少した。両者は第2報告書のDD目標帯より低いロット側を採用しており、全戦略を同一リスクの収益順位として扱わない。','',
        '以上は今回の同じ過去データ・選定済みロットにおける相対評価であり、実運用候補の確定や新しい合否基準ではない。','',
        '### 2.2 4WFの参考合計NP','',chart('np','水準別4WF参考合計NP'),'',
        'NP（Net Profit）は純損益、単位はUSD。**各WFを3,000 USDから独立開始した4件の単純合計**であり、約1年間の連続バックテストでも複利運用でもない。','',
        *table(['戦略','lot','S=0','S=2','S=5','S=10','S=10差','S=10変化率'],[
            [sid,LOTS[sid],*[f'{summary[sid][s]["np"]:.2f}' for s in LEVELS],
             f'{summary[sid][10]["np"]-summary[sid][0]["np"]:+.2f}',
             f'{(summary[sid][10]["np"]/summary[sid][0]["np"]-1)*100:+.2f}%'] for sid in STRATEGIES]),'',
        chart('delta','加工後NPからS0を引いた差'),'',
        '差 = 加工後のNet Profit − S=0のNet Profit。プラスは増益、マイナスは減益。差の図ではS=0の棒を描かないため、S=0の凡例も設けていない。','',
        '### 2.3 WF別の変化と非単調性','',chart('wf-delta','S10のWF別NP差'),'',
        'daily_lのWF3はS=5で67.21 USD、S=10で182.97 USDとなり、全体合計の戻りに大きく関係する。一方WF4はS=5で53.96、S=10で50.68 USDである。合計だけではこの期間差が隠れる。','',
        '価格を変えるとエントリー/決済のタイミングや到達するTP/SL、追従する注文条件などが変わり得る。これが「経路依存性」で、途中の価格・状態の違いがその後の取引結果を変える性質をいう。本書では個別EA内部の分岐原因を特定していないため、非単調な増益を「スリッページが利益を改善する」と解釈しない。','',
        '### 2.4 DDと黒字期間数','',chart('dd','水準別四半期DD最大'),'',
        *table(['戦略','DD S=0','DD S=2','DD S=5','DD S=10','黒字WF数 S=0/2/5/10'],[
            [sid,*[f'{summary[sid][s]["dd"]:.2f}%' for s in LEVELS],
             ' / '.join(f'{summary[sid][s]["positive"]}/4' for s in LEVELS)] for sid in STRATEGIES]),'',
        'DDはEquity（含み損益を含む有効証拠金）の下落率。上表は各水準の4WF中の最大値で、年間DDではない。第2報告書の約5%は過去年間テストでのロット選定目標であり、5%到達時の強制決済ではない。','',
        '事前の[リスク基準](../decisions/20260903_ubs_same_risk_criterion.md)にある7.5%警告値を超えたのは、goldtradepro_aのWF4全4水準とxau_h1_c5のWF2/S=10の計5件。7.5%は統計的安全限界でもMT5標準の決済条件でもない。','',
        *table(['赤字ケース','S','NP USD','DD %'],[
            [r['strategy_id']+' / '+r['wf'],r['stress_points'],f'{r["metrics"]["net_profit"]:.2f}',f'{r["metrics"]["equity_drawdown_percent"]:.2f}']
            for r in rows if r['metrics']['net_profit']<0]),'',
        '### 2.5 取引系列と取引数','',
        'deal系列は約定を日時順に並べたもの。本プロジェクトのdeal系列SHA-256は、日時・売買方向・約定価格を正規化・ソートし、同一約定の重複数も含めて計算する。同時刻内の行順、数量、新規/決済区分そのものはこのハッシュの比較対象ではない。新規約定の数量は別の固定lot監査で確認する。同じ取引数でも各約定の価格などが異なればハッシュは変わる。','',
        *table(['戦略','Trades S=0/2/5/10','系列一致WF数 S=2/5/10','Trades変化WF数 S=2/5/10'],[
            [sid,' / '.join(str(summary[sid][s]['trades']) for s in LEVELS),
             ' / '.join(f'{summary[sid][s]["same"]}/4' for s in LEVELS[1:]),
             ' / '.join(f'{summary[sid][s]["trade_changes"]}/4' for s in LEVELS[1:])] for sid in STRATEGIES]),'',
        '正ストレス84件のdeal系列はすべて各自のS=0と異なった。今回はBid/Askの価格加工を行っているため、価格が変わるだけでもこの不一致は起こる。全84件でエントリー判断が変わった、または7戦略が独立であるという証明ではない。Tradesの変化は長期型2戦略の計9件で観測した。','',
        '## 3. 詳細','', '### 3.1 条件・ロット・期間','',
        *table(['項目','条件'],[
            ['EA','Ultimate Breakout System v7.5 demo。選定済みsetと前段階のv7.5互換設定を保持'],
            ['テスター','H1 / Every tick based on real ticks / No Delay'],
            ['初期入金等','3,000 USD / USD / 1:500'],
            ['固定lot方式','Risk=0、AdjustLotsizeToVariableValues=false、StartLots=MaxLots=採用lot'],
            ['件数','7戦略 × 4WF × 4水準 = 112件。各戦略16件'],
            ['別枠','通常銘柄基準更新28件、診断/試行、カスタムデータ生成は112件に含めない'],
            ['実行負荷','BelowNormal、最大4論理CPU、直列。今回の報告書作成では再計算なし']]),'',
        *table(['戦略','固定lot','第2報告書の年間DD','採用理由'],[
            [sid,LOTS[sid],dd,why] for sid,dd,why in zip(STRATEGIES,
            ('4.90%','5.29%','4.75%','4.88%','10.86%','3.84%','3.89%'),
            ('目標帯内','目標帯内','目標帯内','目標帯内','最低lotでも上限超過','lot刻みの下側','lot刻みの下側'))]),'',
        '目標帯は年間DD 4.5～5.5%。各戦略で全WF・全Sに同じlotを使い、ストレス条件ごとにDDを揃え直していない。元lotが異なるため、価格差の金額影響も全戦略で同額ではない。','',
        *table(['WF','従来と同じ指定期間'],WF_PERIODS.items()),'',
        'ToDateは末日00:00の終了境界。例えばWF1は2025-07-01 00:00から2025-09-30 00:00までで、9月30日終日を含まない。4期をつないだ連続保有の損益や期境界をまたぐDDは測定していない。','',
        '### 3.2 S=0再現確認と基準更新','',
        '初回のS=0確認で、xau_h1_c5/WF1は過去基準NP 5.53 USDに対し5.34 USDとなった。通常XAUUSDでも5.34 USDとなり、deal系列・Commission・deal Profitは一致、差−0.19 USDはSwapだった。カスタム価格加工固有の差とは認められず、具体的なSwap仕様の変化源は未特定である。','',
        '利用者承認のもと通常XAUUSDを28件再取得して今回専用の基準を作成した。旧基準との差は各ケース−0.40～+0.01 USDでSwapのみに帰属した。過去の報告書・結果は変更していない。本書のS=0合計が前報告書の遅延なし合計とわずかに異なるのはこのためである。','',
        '新基準に対するS=0全28件はTrades・deal系列一致、NP・DD・Commission・Swap・deal Profit差が0。所定ゲートはNP差max(0.10 USD,基準の0.5%)以内、DD差0.01 percentage point以内、PF/RF/Sharpe差0.01以内で、差を理由に緩和していない。','',
        'S=2/5/10は監査済みS=0カスタム銘柄から生成し、比較中の最新Swap仕様再取得を避けた。各水準の元ティック276,996,989件・M1 883,909本のハッシュがS=0と一致することを確認した。生成対象は暖機履歴を含む2024-01-01～2026-07-01（終了排他）である。','',
        'S=5の試行ではM1先頭日欠落が起きたが、監査で不採用にした。読み込み再試行を補強し、短期診断とS=5再生成に成功した成果物のみ採用した。失敗runは保持し、集計に混ぜていない。','',
        '### 3.3 会計内訳から見たS=10の差','',
        *table(['戦略','deal Profit差','Commission差','Swap差','NP差'],[
            [sid,*[f'{summary[sid][10][k]-summary[sid][0][k]:+.2f}' for k in ('gross','commission','swap','np')]] for sid in STRATEGIES]),'',
        '4WF参考合計、USD。NP = deal Profit合計 + Commission + Swap（符号付き）。長期型以外はCommission・Swapが同じで、差はdeal Profitにある。長期型では取引数・保有経路の変化もあり、CommissionやSwapの差を含む。各dealのProfit等は元HTMLに保存されている。この内訳だけではEA内部のどの判断が差を生んだかまでは特定できない。','',
        '### 3.4 データ品質・解釈上の限界','',
        '- 保存manifestとresultのSHA-256、112件の一意性、元HTMLの指標/deal、入力set、固定entry lot、既存S=0と生成物の対応を再監査した。正常実行と黒字/低リスクは別判定である。',
        '- WF1は既知の実ティック欠損24分をM1から補完している。既存の絶対数24分以下・比率0.03%以下の品質条件を維持。全時刻が実ティックという意味ではなく、7戦略・複数水準で欠損時間を累積するものではない。',
        '- 固定の対称スプレッド拡大は、現実の非対称スリッページ、価格改善、注文拒否、板の厚さ、通信遅延、時間変動コストを網羅しない。BidのM1を下げることによる指標・注文条件への影響も含む。',
        '- S=0からの損益変化が小さくても、実運用で同じ価格を得られる保証はない。増益ケースもコスト増加が有利であるという証拠ではない。',
        '- 期間は第2報告書のロット選定に使った過去データと重複し、元setの厳密な最適化期間も不明。純粋な未知期間・独立したアウトオブサンプル成績とは断定しない。',
        '- 今回は各条件1回の決定的ストレス比較で、反復誤差や信頼区間を推定していない。S間を補間して損益分岐のスリッページ量を断定しない。',
        '- 7戦略の同時稼働・ポートフォリオDD・証拠金競合や、実取引の第3層測定は対象外。','',
        '### 3.5 全112件の指標','',
        'NP=純損益USD、Trades=取引数、PF=Profit Factor、RF=Recovery Factor、DD=Equity DD%。PF/RF/Sharpeを合計・平均して年間値にしていない。']
    for sid in STRATEGIES:
        lines += ['',f'#### {sid}','',*table(['WF','S','NP','Trades','PF','RF','Sharpe','DD %'],[
            [r['wf'],r['stress_points'],f'{r["metrics"]["net_profit"]:.2f}',r['metrics']['trades'],
             *[f'{r["metrics"][k]:.2f}' if r['metrics'][k] is not None else '—' for k in
               ('profit_factor','recovery_factor','sharpe_ratio','equity_drawdown_percent')]]
            for r in rows if r['strategy_id']==sid])]
    lines += ['', '### 3.6 保存先・追跡と再生成','',
        f'- 正ストレス84件：`{run.as_posix()}/run_manifest.json`',
        f'- S=0の28件：`{ZERO.as_posix()}/run_manifest.json`',
        f'- 今回の通常銘柄基準：`{BASELINE.as_posix()}/run_manifest.json`',
        '- 各manifestは個別runのresult.jsonとSHA-256を参照し、resultからHTML・INI・set・ログへ追跡できる。deal系列SHA-256の実値は各resultのdeal_audit.deal_sequence_sha256。本書の系列一致数はその比較結果である。',
        '- 本書と4枚のSVGはdoc/reports以下、生データはdata以下でGit管理外。既存の報告書・利用者の編集は変更していない。',
        '- [実行計画・変更履歴](../plans/20260910_ubs_transaction_cost.md) / [報告書作成計画](../plans/20260911_ubs_transaction_cost_report.md)','',
        '```powershell','uv run python -m mt5_ea_validator.ubs_cost_report --output doc/reports/ubs_transaction_cost_comparison_new.md','```','',
        '再生成は原データがある同じプロジェクト環境で行う。既存のMarkdownまたはassetsフォルダがあれば上書きを拒否するため、未使用の出力名を指定する。']
    return lines


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,default=RUN)
    p.add_argument('--output',type=Path,default=Path('doc/reports/ubs_transaction_cost_comparison.md'))
    p.add_argument('--inspect',action='store_true')
    a=p.parse_args()
    print(json.dumps(summarize(load_rows(a.run)) if a.inspect else build(a.output,a.run),ensure_ascii=False,indent=2))
