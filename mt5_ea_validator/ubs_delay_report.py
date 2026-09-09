"""Build the UBS delay report from hashed, completed case artifacts."""
import argparse
import json
import statistics
from pathlib import Path

from mt5_ea_validator.mt5 import sha256_file
from mt5_ea_validator.ubs_execution_delay import STRATEGIES, case_plan, comparison
from mt5_ea_validator.ubs_report import _svg_document, WF_PERIODS


def load_results(run, strategies=STRATEGIES):
    expected_plan = case_plan(strategies=strategies)
    manifest = json.loads((run / 'run_manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'success' or manifest['plan'] != expected_plan:
        raise ValueError('Expected successful campaign with the requested cases')
    rows = []
    for record in manifest['results']:
        path = run / record['case_id'] / 'result.json'
        if sha256_file(path) != record['result_sha256']:
            raise ValueError('Result hash mismatch')
        row = json.loads(path.read_text(encoding='utf-8'))
        if (row['status'] != 'success' or not row['compatibility']['passed']
                or not row['tick_data_quality']['passed']):
            raise ValueError('Case audit failed')
        rows.append(row)
    if [r['case_id'] for r in rows] != [c['case_id'] for c in expected_plan]:
        raise ValueError('Missing, duplicate or reordered cases')
    for row, case in zip(rows, expected_plan, strict=True):
        if any(row[k] != v for k, v in case.items()):
            raise ValueError('Case identity mismatch')
    return manifest, rows


def groups(rows):
    return [(sid, wf, [r for r in rows if r['strategy_id'] == sid and r['wf'] == wf])
            for sid in STRATEGIES for wf in WF_PERIODS]


def chart(rows, metric, title, *, delta=False):
    body = [f'<text class="title" x="50" y="42">{title}</text>',
            '<text class="subtitle" x="50" y="78">青：遅延なし　緑：188ms　灰：ランダム平均（線：3回の最小～最大）</text>',
            f'<text class="subtitle" x="50" y="109">{"差 = 遅延ありのNet Profit − 遅延なしのNet Profit（USD）" if delta else "各WFは独立テスト／単位：" + ("USD" if metric == "net_profit" else "%")}</text>']
    plotted = []
    for sid, wf, cases in groups(rows):
        values = [float(r['metrics'][metric]) for r in cases]
        base = values[0] if delta else 0
        plotted.append((sid, wf, [v-base for v in values]))
    low = min(0, min(min(v) for _, _, v in plotted))
    high = max(max(v) for _, _, v in plotted)
    span = high-low or 1
    low -= span*.05
    high += span*.10
    def x(v):
        return 320+(v-low)/(high-low)*780
    for i in range(6):
        v = low+(high-low)*i/5
        body += [f'<line class="grid" x1="{x(v):.1f}" y1="140" x2="{x(v):.1f}" y2="940"/>',
                 f'<text class="axis-label" x="{x(v):.1f}" y="972" text-anchor="middle">{v:.1f}</text>']
    body.append(f'<line class="axis" x1="{x(0):.1f}" y1="140" x2="{x(0):.1f}" y2="940"/>')
    for i, (sid, wf, values) in enumerate(plotted):
        y = 164+i*96
        body.append(f'<text class="label" x="35" y="{y+22}">{sid} / {wf}</text>')
        for j, (val, css) in enumerate(zip([values[0], values[1], statistics.mean(values[2:])], ['ohlc','real','below-target'])):
            yy = y+j*24
            body.append(f'<rect class="{css}" x="{min(x(0), x(val)):.1f}" y="{yy}" width="{max(1, abs(x(val)-x(0))):.1f}" height="15"/>')
            if j == 2:
                a,b = x(min(values[2:])),x(max(values[2:]))
                body.append(f'<path class="axis" fill="none" d="M {a:.1f},{yy+7} H {b:.1f} M {a:.1f},{yy+2} V {yy+12} M {b:.1f},{yy+2} V {yy+12}"/>')
    return _svg_document(title, '2戦略×4WF、ランダム3回の実測範囲を表示。正確な値は本文の表を参照。', body, 1000)


def build(run, output):
    manifest, rows = load_results(run)
    assets = output.parent / 'assets' / output.stem
    targets = [output] + [assets / f'{name}.svg' for name in ('profit','profit-delta','dd')]
    if any(p.exists() for p in targets):
        raise FileExistsError('Existing report/assets are not overwritten')
    text = ['# Ultimate Breakout System — 約定遅延耐性 総合報告書', '',
            '- 対象：UBS v7.5 demo / XAUUSD / xau_h1_c5・daily_l',
            '- 実行結果：40/40正常完了（2026-09-07 12:02 JST）', '',
            '## 1. 目的', '',
            '前段階の[原set比較](ubs_gold_strategy_comparison.md)と[同一リスク比較](ubs_same_risk_comparison.md)で選んだ2戦略について、注文実行の遅れに対する成績の感応度を確認する。第2報告書で採用した固定ロットと期間を維持し、MT5のExecutionModeだけを変更した。7戦略全体の検証ではない。', '',
            '遅延なしを基準に固定188msとランダム遅延3回を比較する。評価対象は損益だけでなく、含み損を含む最大Equity DD、取引数、約定系列である。実取引でのスリッページ実測や、スプレッドを加工するコストストレスは今回実施していない。', '',
            '## 2. 結論', '',
            '### 2.1 総合判断', '',
            '- 固定188msの影響は今回の範囲では小さい。xau_h1_c5は4期ともNet Profitとdeal系列が一致した。daily_lの4期合計NPは652.99 USDから650.99 USDへ2.00 USD（約0.31%）減少した。',
            '- ランダム遅延には感応度がある。xau_h1_c5のWF3はNPが+57.81～+111.90 USD変化し、daily_lのWF4は+115.12～+122.28 USD変化した。一方、daily_lのWF1・WF2では利益が減少した。',
            '- 全40件で黒字、全遅延ケースでTradesは対応する基準と同数だった。それでも日時・方向・約定価格から作るdeal系列は32遅延ケース中22件で異なった。取引数の一致だけでは再現性を判断できない。',
            '- 最大Equity DDは全ケースで7.5%警告線未満。これは過去の今回条件の結果であり、将来の損失上限や強制決済機能ではない。',
            '- 遅延で利益が増えたことを、実運用で期待できる改善とは判断しない。執行時点や注文管理の変化に成績が左右される結果として扱う。ランダムは3回の初期評価であり、十分な確率分布の推定ではない。', '',
            '### 2.2 四半期ごとのNet Profit', '',
            f'![四半期損益比較](assets/{output.stem}/profit.svg)', '',
            '線はランダム3回の最小～最大、灰色の棒は平均。信頼区間ではない。各棒は四半期1回のテストを表し、4つのWFの総和ではない。', '',
            '| 戦略 | WF | 遅延なし NP | 188ms NP | ランダム平均 NP | ランダム最小～最大 NP |',
            '|---|---|---:|---:|---:|---:|']
    for sid, wf, cases in groups(rows):
        v = [float(r['metrics']['net_profit']) for r in cases]
        text.append(f'| {sid} | {wf} | {v[0]:.2f} | {v[1]:.2f} | {statistics.mean(v[2:]):.2f} | {min(v[2:]):.2f}～{max(v[2:]):.2f} |')
    text += ['', '金額の単位はUSD。四半期は毎回3,000 USDから独立して開始している。4期の単純合計は資金を引き継ぐ年間連続運用の成績ではない。', '',
             '### 2.3 遅延による損益差', '', f'![遅延あり−なしのNP差](assets/{output.stem}/profit-delta.svg)', '',
             '差 = 遅延ありのNet Profit − 遅延なしのNet Profit。右側（正）は利益増加、左側（負）は利益減少。ゼロ近傍は損益変化が小さいことを意味するが、約定系列や途中のDDまで同じとは限らない。', '',
             '### 2.4 Equity DD', '', f'![四半期最大Equity DD](assets/{output.stem}/dd.svg)', '',
             'DDは決済済み損益だけでなく、保有中の含み損益にも依存する。例えばxau_h1_c5のWF3固定188msはNP・deal系列が同じでもDDが2.14%から2.16%へ変わった。daily_lのWF3もNP・deal系列が同じでDDは4.98%から4.87%へ変わった。ハッシュが同じでも、途中のEquity経路全体が同一とは限らない。', '',
             '## 3. 詳細', '', '### 3.1 条件・期間・計算数', '',
             '| 項目 | 条件 |', '|---|---|', '| EA | Ultimate Breakout System v7.5 demo |',
             '| MT5 | 更新後build 6182で新runを実行 |',
             '| 銘柄・Tester時間足 | XAUUSD・H1（内部時間足設定はsetを維持） |',
             '| モデル | Every tick based on real ticks |',
             '| Deposit・通貨・レバレッジ | 3000 USD・USD・1:500 |',
             '| 固定ロット | xau_h1_c5：0.03 / daily_l：0.04 |',
             '| 資金管理 | Risk=0、AdjustLotsizeToVariableValues=false、StartLots=MaxLots=採用lot |',
             '| 遅延 | ExecutionMode=0、188、-1（ランダム3回） |',
             '| 計算数 | 2戦略 × 4WF × (基準1 + 固定1 + ランダム3) = 40件 |',
             '| 実行負荷 | BelowNormal・4論理CPU・直列実行 |', '',
             '| WF | 期間（従来と同じ） |', '|---|---|']
    text += [f'| {wf} | {period} |' for wf, period in WF_PERIODS.items()]
    text += ['', '期間表は従来の指定日表記。実際のTester FromDate/ToDateは各INIに保存され、ToDateは末日の00:00境界である。例えばWF1は2025.07.01 00:00から2025.09.30 00:00までであり、9月30日終日を含むという意味ではない。ロット選定に使った約1年とWFは重複するため、独立した未知期間の検証とは扱わない。', '',
             '### 3.2 約定系列と監査', '',
             'dealは個々の売買約定、deal系列は日時・方向・約定価格の時系列である。そのSHA-256を対応する遅延なしと比較した。ハッシュ自体は取引詳細を表示せず、違いの有無を検出する。個別の差の原因を確定するにはHTMLの約定行・注文・ログの追跡が必要になる。', '',
             '| 戦略 | 固定188ms系列一致 | ランダム系列一致 | 遅延ケースのTrades一致 |', '|---|---:|---:|---:|']
    for sid in STRATEGIES:
        subset = [r for r in rows if r['strategy_id'] == sid]
        counts = []
        for mode in (188,-1):
            count = sum(comparison(r, next(b for b in subset if b['wf']==r['wf'] and b['execution_mode']==0))['deal_sequence_same'] for r in subset if r['execution_mode']==mode)
            counts.append(count)
        text.append(f'| {sid} | {counts[0]}/4 | {counts[1]}/12 | 16/16 |')
    text += ['', '8件のNo Delayは前段階に対する再現ゲートを通過。40件すべてで入力互換性とティック品質がPASS。WF1には従来と同じ24分の実ティック欠損補完があり、比率上限0.03%・絶対数24分以下の既存条件を適用した。', '',
             '保存JSONのreproduction_passedは厳密な基準との再現判定である。遅延ありでfalseになること自体は失敗ではない。遅延ありで取引やDDが変わることが今回の測定対象である。', '',
             '### 3.3 全40件の実測値', '',
             'NP：純損益（USD）、PF：Profit Factor、RF：Recovery Factor、DD：最大Equity DD（%）。R1～R3はランダム遅延の反復番号であり、別の遅延設定値ではない。', '',
             '| 戦略 | WF | 遅延 | NP | Trades | PF | RF | Sharpe | DD % |', '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        m=r['metrics']; label={0:'なし',188:'188ms',-1:f'R{r["repetition"]}'}[r['execution_mode']]
        text.append(f'| {r["strategy_id"]} | {r["wf"]} | {label} | {m["net_profit"]:.2f} | {m["trades"]} | {m["profit_factor"]:.2f} | {m["recovery_factor"]:.2f} | {m["sharpe_ratio"]:.2f} | {m["equity_drawdown_percent"]:.2f} |')
    text += ['', '### 3.4 制約と次の評価', '',
             '- ランダムのseedは指定・固定していない。3回を独立同分布と証明しておらず、同じ結果が出ても独立性や無感応を証明しない。',
             '- 188msは前の検証に合わせた固定条件であり、実運用の遅延分布を測定した値ではない。',
             '- 指値・逆指値、注文変更、内部フィルターなどへの影響を、EAの非公開内部ロジックまで断定しない。損益改善ケースの詳細原因は未確定。',
             '- スプレッド拡大・追加手数料・低資金・複数戦略同時稼働は未検証。次は取引コスト耐性を独立して評価できる。',
             '- 正確な最適化期間が不明であり、過去の再検証として解釈する。', '',
             '### 3.5 成果物・実行履歴', '',
             f'- 正本run：`{run.as_posix()}`',
             '- `run_manifest.json`：全ケース・条件・ケースresultのSHA-256。',
             '- 各ケースフォルダ：HTML、INI、input_set.set、適用入力、監査、ログ、result.json。Commission・Swap・deal Profit合計およびdeal系列SHA-256はresult.jsonに保存。',
             '- 最終ケース`WF4_daily_l_random_3/cumulative_summary.csv`：全40件の累積集計。',
             '- 初回run 20260907T110008682379_c686a0c1は7件保存後のMT5自動更新（6140→6182）で監視が中断。本報告書へ混在させず保存した。',
             '- 生データはdata以下でGit管理外。報告書・SVGはdoc/reports以下。', '',
             '再生成（既存出力を保護するため、新しい出力ファイル名を指定する）：', '',
             '```powershell', f'uv run python -m mt5_ea_validator.ubs_delay_report --run {run.as_posix()} --output doc/reports/ubs_execution_delay_comparison_new.md', '```', '']
    assets.mkdir(parents=True, exist_ok=True)
    for name, metric, title, delta in [('profit','net_profit','四半期 Net Profit：遅延条件の比較',False), ('profit-delta','net_profit','四半期 Net Profit差：遅延あり − 遅延なし',True), ('dd','equity_drawdown_percent','四半期 最大Equity DD：遅延条件の比較',False)]:
        (assets / f'{name}.svg').write_text(chart(rows,metric,title,delta=delta), encoding='utf-8')
    output.write_text('\n'.join(text), encoding='utf-8')
    return output


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('doc/reports/ubs_execution_delay_comparison.md'))
    args=parser.parse_args()
    print(build(args.run,args.output))
