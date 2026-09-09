"""Audited seven-strategy delay report; reuse the existing result reader and SVG theme."""
from __future__ import annotations

import argparse
import html
import json
import statistics as stats
from decimal import Decimal
from pathlib import Path

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.report import parse_report, parse_deal_audit, parse_entry_volume_audit
from mt5_ea_validator.ubs_delay_report import load_results
from mt5_ea_validator.ubs_execution_delay import STRATEGIES, REMAINING_STRATEGIES, comparison
from mt5_ea_validator.ubs_report import STRATEGIES as ALL, WF_PERIODS, _svg_document


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def combine(first, remaining):
    a, ar = load_results(first)
    b, br = load_results(remaining, REMAINING_STRATEGIES)
    for key in ('conditions', 'ea_sha256', 'baseline_sha256'):
        if a[key] != b[key]:
            raise ValueError(f'Campaign conditions differ: {key}')
    baseline_path = Path(a['baseline_manifest'])
    if sha256_file(baseline_path) != a['baseline_sha256']:
        raise ValueError('Baseline changed')
    baseline = read_json(baseline_path)
    baselines = {(r['strategy_id'], r['wf']): r for r in baseline['results']}
    rows = ar + br
    if len(rows) != 140 or len({r['case_id'] for r in rows}) != 140:
        raise ValueError('Expected 140 unique cases')
    lookup = {(r['strategy_id'], r['wf']): r for r in rows if r['execution_mode'] == 0}
    for r in rows:
        report = Path(r['report_file'])
        if parse_report(report).to_dict() != r['metrics']:
            raise ValueError('HTML metrics differ from result')
        if parse_deal_audit(report).to_dict() != r['deal_audit']:
            raise ValueError('HTML deals differ from result')
        if sha256_file(report.parent / 'input_set.set') != r['set_sha256']:
            raise ValueError('Input set changed')
        base = baselines[(r['strategy_id'], r['wf'])]
        if r['set_sha256'] != base['selected_set_sha256']:
            raise ValueError('Baseline set differs')
        volume = parse_entry_volume_audit(report).to_dict()
        for key in ('minimum_entry_volume', 'maximum_entry_volume'):
            if Decimal(str(volume[key])) != Decimal(r['selected_lot']):
                raise ValueError('Fixed lot audit failed')
        quality = r['tick_data_quality']
        if 'fallback_minute_count' not in quality or int(quality['fallback_minute_count'] or 0) > 24:
            raise ValueError('Tick fallback limit failed')
        if r['execution_mode'] == 0 and not comparison(r, base)['reproduction_passed']:
            raise ValueError('Baseline reproduction failed')
        r['delta'] = comparison(r, lookup[(r['strategy_id'], r['wf'])])
        r['source_run'] = first.name if r['strategy_id'] in STRATEGIES else remaining.name
    rows.sort(key=lambda r: (ALL.index(r['strategy_id']), list(WF_PERIODS).index(r['wf']),
                            {0: 0, 188: 1, -1: 2}[r['execution_mode']], r['repetition']))
    return [a, b], rows


def grouped(rows, sid, wf=None, mode=None):
    return [r for r in rows if r['strategy_id'] == sid and (wf is None or r['wf'] == wf)
            and (mode is None or r['execution_mode'] == mode)]


def summarize(rows):
    summaries = []
    for sid in ALL:
        rr = grouped(rows, sid)
        base, fixed, random = [grouped(rows, sid, mode=m) for m in (0, 188, -1)]
        np0 = sum(r['metrics']['net_profit'] for r in base)
        np1 = sum(r['metrics']['net_profit'] for r in fixed)
        npr = sum(stats.mean(r['metrics']['net_profit'] for r in grouped(rows, sid, wf, -1)) for wf in WF_PERIODS)
        summaries.append(dict(strategy=sid, lot=rr[0]['selected_lot'], baseline=np0,
            fixed=np1, random=npr, fixed_delta=np1-np0, random_delta=npr-np0,
            dd_base=max(r['metrics']['equity_drawdown_percent'] for r in base),
            dd_fixed=max(r['metrics']['equity_drawdown_percent'] for r in fixed),
            dd_random=max(r['metrics']['equity_drawdown_percent'] for r in random),
            fixed_same=sum(r['delta']['deal_sequence_same'] for r in fixed),
            random_same=sum(r['delta']['deal_sequence_same'] for r in random),
            changed_trades=sum(r['delta']['trades_delta'] != 0 for r in fixed+random),
            random_min_delta=min(r['delta']['net_profit_delta'] for r in random),
            random_max_delta=max(r['delta']['net_profit_delta'] for r in random)))
    return summaries


def bars(summary, kind):
    spec = {
        'profit': ('4期の参考合計 Net Profit', ('baseline', 'fixed', 'random'), 'USD',
                   '各WFの独立テストを合計／灰色は各WFのランダム平均の合計'),
        'delta': ('4期の参考合計 Net Profit差', ('fixed_delta', 'random_delta'), 'USD',
                  '差 = 遅延ありのNet Profit − 遅延なしのNet Profit'),
        'dd': ('各条件で観測した四半期DDの最大値', ('dd_base', 'dd_fixed', 'dd_random'), '%',
               '青・緑は各4件の最大／灰色はランダム12件の最大（年間DDではない）'),
    }
    title, keys, unit, subtitle = spec[kind]
    vals = [s[k] for s in summary for k in keys]
    lo, hi = min(0, min(vals)), max(vals)
    span = hi-lo or 1
    lo -= span*.04; hi += span*.12
    def x(v):
        return 285+(v-lo)/(hi-lo)*795
    legend = ('緑：188ms　灰：ランダム' if kind == 'delta'
              else '青：遅延なし　緑：188ms　灰：ランダム')
    body = [f'<text class="title" x="40" y="42">{title}</text>',
            f'<text class="subtitle" x="40" y="78">{subtitle}</text>',
            f'<text class="subtitle" x="40" y="108">{legend}　／　数値は棒の右に表示</text>']
    for i in range(6):
        v = lo+(hi-lo)*i/5
        body += [f'<line class="grid" x1="{x(v):.1f}" y1="140" x2="{x(v):.1f}" y2="830"/>',
                 f'<text class="axis-label" x="{x(v):.1f}" y="865" text-anchor="middle">{v:.1f}</text>']
    body.append(f'<line class="axis" x1="{x(0):.1f}" y1="140" x2="{x(0):.1f}" y2="830"/>')
    for i, s in enumerate(summary):
        y=158+i*96
        body.append(f'<text class="label" x="30" y="{y+27}">{s["strategy"]}</text>')
        for j,k in enumerate(keys):
            v=s[k]; yy=y+j*25
            css=('real','below-target')[j] if kind=='delta' else ('ohlc','real','below-target')[j]
            body += [f'<rect class="{css}" x="{min(x(0),x(v)):.1f}" y="{yy}" width="{max(.7,abs(x(v)-x(0))):.1f}" height="17"/>',
                     f'<text class="value" x="{max(x(0),x(v))+9:.1f}" y="{yy+15}">{v:.2f}</text>']
    if kind == 'dd':
        body.append(f'<line class="warning-line" x1="{x(7.5):.1f}" y1="140" x2="{x(7.5):.1f}" y2="830"/>')
    body.append(f'<text class="subtitle" x="40" y="909">単位：{unit}／{"赤破線：事前設定した警告基準7.5%（強制決済条件ではない）" if kind=="dd" else "ロット・過去DDは全戦略で完全一致していないため、利益額だけで順位付けしない"}</text>')
    return _svg_document(title, subtitle, body, 940)


def heatmap(rows):
    body = ['<text class="title" x="40" y="42">ランダム遅延の四半期別 Net Profit差</text>',
            '<text class="subtitle" x="40" y="78">差 = ランダム3回の平均Net Profit − 遅延なしのNet Profit（USD）</text>',
            '<text class="subtitle" x="40" y="109">各セル：平均差／下段：3回の差の最小～最大　青＝増加、赤＝減少</text>']
    for j,wf in enumerate(WF_PERIODS):
        body.append(f'<text class="label" x="{390+j*210}" y="150" text-anchor="middle">{wf}</text>')
    for i,sid in enumerate(ALL):
        y=172+i*85
        body.append(f'<text class="label" x="30" y="{y+38}">{sid}</text>')
        for j,wf in enumerate(WF_PERIODS):
            values=[r['delta']['net_profit_delta'] for r in grouped(rows,sid,wf,-1)]
            mean=stats.mean(values); xx=292+j*210
            css='warning-cell' if mean<0 else 'heat-1'
            body += [f'<rect class="{css}" x="{xx}" y="{y}" width="196" height="73" rx="4"/>',
                     f'<text class="value" x="{xx+98}" y="{y+29}" text-anchor="middle">{mean:+.2f}</text>',
                     f'<text class="axis-label" x="{xx+98}" y="{y+56}" text-anchor="middle">{min(values):+.2f} ～ {max(values):+.2f}</text>']
    body.append('<text class="subtitle" x="40" y="817">線や範囲は信頼区間ではなく、測定した3回だけの範囲です。</text>')
    return _svg_document('ランダム遅延の四半期別損益差', '7戦略×4WF。平均と実測範囲。', body, 845)


def build(first, remaining, output):
    manifests, rows = combine(first, remaining)
    summary = summarize(rows)
    assets=output.parent/'assets'/output.stem
    if output.exists() or assets.exists():
        raise FileExistsError('Existing report/assets are not overwritten')
    delayed=[r for r in rows if r['execution_mode'] != 0]
    changed=sum(not r['delta']['deal_sequence_same'] for r in delayed)
    trades=sum(r['delta']['trades_delta']!=0 for r in delayed)
    negative=[r for r in rows if r['metrics']['net_profit']<0]
    warnings=[r for r in rows if r['metrics']['equity_drawdown_percent']>7.5]
    lines=['# Ultimate Breakout System Gold 7戦略 — 約定遅延耐性 総合報告書','',
        '- 対象：UBS v7.5 demo / XAUUSD用7戦略',
        '- 計算完了：先行40件＋追加100件＝140/140件成功（最終完了2026-09-09 14:32 JST）',
        '- 本書は[先行2戦略の報告書](ubs_execution_delay_comparison.md)を包含した7戦略版である。','',
        '## 1. 目的','',
        '### 1.1 検証の位置づけ','',
        '[第1報告書](ubs_gold_strategy_comparison.md)では原setでの収益性・モデル差を、[第2報告書](ubs_same_risk_comparison.md)では固定ロットで過去の最大Equity DDを約5%へ近づけた結果を比較した。本書は、その採用ロットで注文の実行が遅れた場合に、損益・DD・取引数・約定系列がどれだけ変わるかを評価する。','',
        '第2報告書ではxau_h1_c5を目標DD帯内での収益首位、daily_lを収益と期間安定性のバランス候補と評価したため、先に2戦略を測定した。ただし好成績と遅延耐性は別の性質である。残り5戦略も追加し、7戦略全体を同じ手順で検証した。','',
        '### 1.2 変更した設定と比較方法','',
        '変更したExecutionModeは、自動実行用INIにおけるMT5ストラテジーテスター設定タブの「延滞」に対応する。EA内部の入力パラメータではない。各戦略のset、固定lot、入金額、期間、ティックモデルは維持した。','',
        '| 条件 | ExecutionMode | 各戦略・各WFでの回数 | 全7戦略・4WF |','|---|---:|---:|---:|',
        '| 遅延なし | 0 | 1回 | 28件 |','| 固定188ms | 188 | 1回 | 28件 |','| ランダム遅延 | -1 | 3回（R1・R2・R3） | 84件 |','| 合計 | — | 5回 | 140件 |','',
        'R1～R3は同じ条件での再実行番号であり、別々の固定遅延値ではない。3回の平均と最小～最大でばらつきを見る。ランダムseedは固定しておらず、3回の統計的独立性や確率分布を証明したものではない。','',
        '## 2. 結果','', '### 2.1 総合判断','',
        '- **先行2戦略だけの評価では見えなかった違いがある。** eは固定188msの純損益が全WFで一致し、ランダムの変化も小さかった。一方、mt5_longterm_eでは固定188msでもWF4の赤字が拡大し、取引数も変化した。',
        '- xau_h1_c5は固定188msの純損益・deal系列が全WFで一致したが、ランダムではWF3の利益が大きく変わった。daily_lもWF4で大きな変化があり、収益上位だから遅延に無感応とはいえない。',
        '- goldtradepro_aはWF4のランダムNPが22.27～102.99 USDと大きくばらついた。さらにNo DelayからDDが13.28%と高く、同一リスク比較の対象としては別扱いが必要である。',
        f'- 140件はすべて実行・入力・ティック監査に成功したが、損益が負のケースは{len(negative)}件。正常実行と黒字は別の判定である。遅延あり112件中、deal系列変更は{changed}件、Trades変更は{trades}件だった。',
        '- 遅延で利益が増えたケースもあるが、実運用の利益改善を保証する結果ではない。注文・約定・決済の経路への感応度として読む。','',
        '### 2.2 7戦略の参考合計と変化','',
        f'![4期参考合計NP](assets/{output.stem}/profit.svg)','',
        '**この図は4つのWFの単純合計であり、約1年間の連続バックテストではない。** 各WFは3,000 USDから独立開始した。ランダムは各WFの3回平均を計算してから4期分を合計している。ランダムの独立した年間3試行ではない。','',
        '| 戦略 | lot | 基準NP合計 | 188ms合計 | 差 | ランダム平均合計 | 差 |','|---|---:|---:|---:|---:|---:|---:|']
    for s in summary:
        lines.append(f'| {s["strategy"]} | {s["lot"]} | {s["baseline"]:.2f} | {s["fixed"]:.2f} | {s["fixed_delta"]:+.2f} | {s["random"]:.2f} | {s["random_delta"]:+.2f} |')
    lines += ['', '金額はUSD。過去年間DDを目標帯4.5～5.5%に収められたのはxau_sr_scalp_h1、xau_h1_c5、daily_l、eの4戦略。mt5_longterm_e・mt5_longterm_jはロット刻みのため低め、goldtradepro_aは最低lotでも高めである。この3戦略を含む利益額の単純順位は同一リスク順位ではなく、各戦略自身の基準からの変化を中心に評価する。','',
        f'![参考合計NP差](assets/{output.stem}/delta.svg)','',
        '差 = 遅延ありのNet Profit − 遅延なしのNet Profit。正は増加、負は減少。合計で相殺される期間差があるため、次のWF別図も確認する。','',
        '### 2.3 ランダム遅延のWF別変化','',f'![ランダムWF別差](assets/{output.stem}/random-wf.svg)','',
        '各セルは四半期の平均差と3回の実測範囲。利益が増えたことを「耐性が高い」と同一視しない。基準からの変化が大きければ、有利方向でも執行条件への依存を示す。','',
        '### 2.4 DDと赤字ケース','',f'![期間別DD最大](assets/{output.stem}/dd.svg)','',
        '図は各戦略の四半期DD最大値。青・緑は各4件、灰はランダム12件の最大を示す。DDを足した値でも年間連続DDでもない。','',
        '| 戦略 | 基準DD最大 % | 188ms DD最大 % | ランダムDD最大 % |','|---|---:|---:|---:|']
    lines += [f'| {s["strategy"]} | {s["dd_base"]:.2f} | {s["dd_fixed"]:.2f} | {s["dd_random"]:.2f} |' for s in summary]
    lines += ['', f'7.5%超は{len(warnings)}件で、すべてgoldtradepro_aのWF4。7.5%は[2026-09-03の決定記録](../decisions/20260903_ubs_same_risk_criterion.md)に事前記載された警告値である。数値選択の詳細根拠は記録されておらず、統計的安全限界・MT5の規定値・強制決済条件ではない。約5%は過去年間DDを調整する目標で、7.5%は結果を分類する別の基準である。','',
        '| 赤字ケース | NP USD | Trades | DD % |','|---|---:|---:|---:|']
    lines += [f'| {r["case_id"]} | {r["metrics"]["net_profit"]:.2f} | {r["metrics"]["trades"]} | {r["metrics"]["equity_drawdown_percent"]:.2f} |' for r in negative]
    lines += ['', '### 2.5 約定系列・取引数の変化','',
        'dealは個々の売買約定、deal系列はその日時・方向・約定価格の時系列である。SHA-256が異なる場合、その系列の少なくとも一部が異なる。ハッシュは差の原因や途中のEquity全体を表すものではない。','',
        '| 戦略 | 固定188ms系列一致 | ランダム系列一致 | Trades変更件数（遅延あり16件中） |','|---|---:|---:|---:|']
    lines += [f'| {s["strategy"]} | {s["fixed_same"]}/4 | {s["random_same"]}/12 | {s["changed_trades"]} |' for s in summary]
    lines += ['', '例えばxau_h1_c5のWF3固定188msはNP・deal系列が同じでもDDが2.14%から2.16%へ変わった。約定系列だけでは、保有中の評価損益や注文変更の全履歴まで同一とは確認できない。','',
        '### 2.6 戦略ごとの読み方','',
        '| 戦略 | 今回の評価 |','|---|---|',
        '| xau_sr_scalp_h1 | 188msのNP変化は小さいが、ランダムでWF3・WF4が変化する。 |',
        '| xau_h1_c5 | 188msのNP・系列は安定。ランダムWF3の大きな利益増加は感応度として扱う。 |',
        '| daily_l | 188msのNP減少は小さい。ランダムではWF1・WF2の減益とWF4の大幅増益が混在。 |',
        '| e | 今回の損益感応度は小さい。取引系列・DDが完全不変という意味ではない。 |',
        '| goldtradepro_a | WF4の高DDとランダム損益の大きなばらつきを併せて考慮する。 |',
        '| mt5_longterm_e | WF3のランダム減益、WF4の188ms赤字拡大、取引数変化に注意。 |',
        '| mt5_longterm_j | 多くの期間でNP・系列が一致するが、WF2ランダムでは取引が1件増加する。 |','',
        '## 3. 詳細','', '### 3.1 条件と期間','',
        '| 項目 | 条件 |','|---|---|',
        '| EA / Tester | Ultimate Breakout System v7.5 demo / MT5 build 6182（実行ログ） |',
        '| 銘柄・時間足 | XAUUSD・H1。内部時間足は元の設定を維持 |',
        '| モデル | Every tick based on real ticks |',
        '| 初期入金・通貨・レバレッジ | 3,000 USD・USD・1:500 |',
        '| lot方式 | Risk=0、AdjustLotsizeToVariableValues=false、StartLots=MaxLots=選択lot |',
        '| 計算数 | 7戦略×4WF×5回＝140件（各戦略20件） |',
        '| 負荷制限 | BelowNormal、4論理CPU、直列実行 |','',
        '**使用ロット一覧（第2報告書から引き継ぎ）**','',
        '今回の全140件には、第2報告書で選定した以下の固定ロットを使用した。各戦略でWF1～WF4・遅延なし・188ms・ランダムのすべてに同じロットを適用し、遅延条件ごとにDDが約5%になるよう再調整していない。','',
        '| 戦略 | 使用固定ロット | 第2報告書の年間Equity DD | 第2報告書での選定結果 |',
        '|---|---:|---:|---|',
        '| xau_sr_scalp_h1 | 0.03 lot | 4.90% | 目標帯内 |',
        '| xau_h1_c5 | 0.03 lot | 5.29% | 目標帯内 |',
        '| daily_l | 0.04 lot | 4.75% | 目標帯内 |',
        '| e | 0.03 lot | 4.88% | 目標帯内 |',
        '| goldtradepro_a | 0.01 lot | 10.86% | 最低ロットでも目標上限超過 |',
        '| mt5_longterm_e | 0.01 lot | 3.84% | ロット刻みにより目標帯の下側を採用 |',
        '| mt5_longterm_j | 0.02 lot | 3.89% | ロット刻みにより目標帯の下側を採用 |','',
        '目標帯は年間DD 4.5～5.5%。この表のDDはロット選定に使った第2報告書の年間テスト値であり、今回の四半期別・遅延ありのDDではない。出典：[第2報告書 2.2](ubs_same_risk_comparison.md#22-約1年ddの調整結果)。','',
        '| WF | 従来と同じ指定期間 |','|---|---|']
    lines += [f'| {wf} | {period} |' for wf,period in WF_PERIODS.items()]
    lines += ['', '実際のINIのToDateは指定末日の00:00である。例えばWF1は2025.07.01 00:00から2025.09.30 00:00までで、9月30日終日を含む意味ではない。全期間はロット選定の過去約1年と重複している。','',
        '### 3.2 四半期別NPの比較表','',
        '| 戦略 | WF | 基準 | 188ms | R1 | R2 | R3 | ランダム平均 |','|---|---|---:|---:|---:|---:|---:|---:|']
    for sid in ALL:
        for wf in WF_PERIODS:
            values=[r['metrics']['net_profit'] for r in grouped(rows,sid,wf)]
            lines.append(f'| {sid} | {wf} | ' + ' | '.join(f'{v:.2f}' for v in values+[stats.mean(values[2:])])+' |')
    lines += ['', '単位：USD。R1～R3は同一条件のランダム反復。','',
        '### 3.3 再現性・データ品質・限界','',
        '- 2runの条件、EA SHA-256、前段階の基準manifest SHA-256が一致。全140件のresultハッシュ、HTMLの損益とdeal監査、入力setハッシュ、固定entry lotを再確認した。',
        '- 遅延なし28件は前段階のTrades・deal系列一致、NP差max(0.10 USD, 基準の0.5%)以内、DD差0.01pp以内の再現ゲートを通過した。',
        '- 全件の入力互換性・ティック監査がPASS。WF1は既知の24分の実ティック欠損を補完しており、既存の比率上限0.03%・絶対数24分以下を適用した。7戦略分の異なる欠損が累積した意味ではない。',
        '- 遅延ありのreproduction_passed=falseは基準との不一致を示す。実行失敗とは区別する。',
        '- Commission・Swap・各deal Profit合計とdeal系列SHA-256は各result.json、個別約定はHTMLに保存されている。個々の損益差の発生原因まで本書で断定していない。',
        '- ランダム3回は初期評価である。範囲は実測最小～最大で、信頼区間や将来の最大損失幅ではない。',
        '- 実取引でのスリッページ測定、スプレッド加工、追加手数料、低資金、同時稼働ポートフォリオは今回の検証範囲外。',
        '- 正確な最適化期間が不明なため、厳密な未知期間の成績とは断定しない。','',
        '### 3.4 全140件の指標','',
        'NP=純損益USD、PF=Profit Factor、RF=Recovery Factor、DD=最大Equity DD%。期間別のPF・RF・Sharpeを合計・平均して年間値としていない。']
    for sid in ALL:
        lines += ['', f'#### {sid}', '', '| WF | 遅延 | NP | Trades | PF | RF | Sharpe | DD % |','|---|---|---:|---:|---:|---:|---:|---:|']
        for r in grouped(rows,sid):
            m=r['metrics']; label={0:'なし',188:'188ms',-1:f'R{r["repetition"]}'}[r['execution_mode']]
            lines.append(f'| {r["wf"]} | {label} | {m["net_profit"]:.2f} | {m["trades"]} | {m["profit_factor"]:.2f} | {m["recovery_factor"]:.2f} | {m["sharpe_ratio"]:.2f} | {m["equity_drawdown_percent"]:.2f} |')
    lines += ['', '### 3.5 保存先・履歴・再生成','',
        f'- 先行2戦略40件：`{first.as_posix()}`', f'- 追加5戦略100件：`{remaining.as_posix()}`',
        '- 各runのrun_manifest.jsonから各ケースのresult.json、HTML、INI、input_set.set、ログへ追跡できる。',
        '- 累積集計CSVは先行runのWF4_daily_l_random_3、追加runのWF4_mt5_longterm_j_random_3に保存。',
        '- 先行の更新前build 6140の中断runは集計から除外した。追加runは利用者指示でケース間停止・再開し、プロセス確認タイムアウト後も確定済み結果を再利用した。',
        '- 生データはdata以下でGit管理外。本書と4枚のSVGはdoc/reports以下。',
        '- 本書の作成ではMT5の再計算は行っていない。','',
        '```powershell', f'uv run python -m mt5_ea_validator.ubs_all_delay_report --first-run {first.as_posix()} --remaining-run {remaining.as_posix()} --output doc/reports/ubs_execution_delay_all_strategies_new.md', '```','',
        '既存の出力を保護するため、再生成時は未使用のファイル名を指定する。','']
    assets.mkdir(parents=True)
    for kind in ('profit','delta','dd'):
        (assets/f'{kind}.svg').write_text(bars(summary,kind),encoding='utf-8')
    (assets/'random-wf.svg').write_text(heatmap(rows),encoding='utf-8')
    output.write_text('\n'.join(lines),encoding='utf-8')
    return summary, rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-run',type=Path,required=True)
    parser.add_argument('--remaining-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('doc/reports/ubs_execution_delay_all_strategies.md'))
    args=parser.parse_args()
    summary, rows=build(args.first_run,args.remaining_run,args.output)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(f'Built {args.output}: {len(rows)} cases')
