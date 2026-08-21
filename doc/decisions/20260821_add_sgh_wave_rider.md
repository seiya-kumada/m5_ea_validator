# Smart Gold Hunter / Wave Rider Capital Stress追加

日付: 2026-08-21

## 変更前

- 実行期間、Deposit、Benchmark、setパスはシナリオ設定で切り替えられる。
- 一方、Scenario検証メッセージ、set検証関数名、レポートファイル名にはQuantum Queen固有の表現が残っている。
- required_set_valuesの必須キーもQuantum Queenの入力名に固定されている。

## 変更理由

利用者の指示により、Smart Gold HunterとWave Rider EA MT5について、WF1～WF4それぞれの3000 USD再現確認と1000 USD Capital Stress Testを実施する。EAごとに入力名・入力数・WFO選択値が異なるため、QQ固有分岐を増やすのではなく設定駆動へ一般化する。

## 正本プロファイルと主要WFO値

共通期間:

- WF1: 2025-07-01～2025-09-30
- WF2: 2025-10-01～2025-12-31
- WF3: 2026-01-01～2026-03-31
- WF4: 2026-04-01～2026-06-30

Smart Gold Hunter:

- `inp_Mode`: WF1=1、WF2=1、WF3=2、WF4=2
- 全WF: `inp_MM=false`、`inp_Lots=0.01`、`inp_UsePropFirmMode=false`、`inp_UseNewsFilter=false`
- 各WFの全39入力は対応するMT5 Testerプロファイルを保持する。

Wave Rider:

- `Inp_RiskProfile`: WF1=2、WF2=2、WF3=0、WF4=0
- 全WF: `Inp_TradeMode=0`、`Inp_Grid_RecoverySpacing=1`
- 全WF: Fixed Lot 0.01、Grid Max Orders 10、Drawdown Mode 0
- 各WFの全89入力は対応するMT5 Testerプロファイルを保持する。

## 手動No Delay / Deposit 3000 USD基準

| EA | WF | Net Profit | Trades | Deals | deal系列SHA-256 |
|---|---|---:|---:|---:|---|
| Smart Gold Hunter | WF1 | +31.08 | 91 | 182 | `862bb5c68a4192473f8f6eade74baec4425c01bec7c8528b942e17730b0abec7` |
| Smart Gold Hunter | WF2 | -113.27 | 90 | 180 | `6bd7ae0bee30fffa3d49e3da4b6e465191eab105be4b16dc992b2c34d9dcf45f` |
| Smart Gold Hunter | WF3 | +113.78 | 33 | 66 | `16d1673d468dd70ff8b4ed5be7be90a8255cfa80e2f956cf35c01577bc5ab41f` |
| Smart Gold Hunter | WF4 | +74.37 | 27 | 54 | `ef2b3054d87ec1c18c7f6d3f6535c59ea9c0aa27c9738d739b17c7b977fdd488` |
| Wave Rider | WF1 | +587.37 | 382 | 764 | `954c2417fbe739cbbb3787e535b2e63af6d75209ba051fe6dbf395cc076cf07b` |
| Wave Rider | WF2 | +603.59 | 461 | 922 | `efab7250ec61dd20d8b278215e7205e99af4ad87796afbba74ce76cceb5c5285` |
| Wave Rider | WF3 | +632.62 | 378 | 756 | `650a70925e656b3c92c72b9cbee2f5678605ac89fcda943531c105569dbd2566` |
| Wave Rider | WF4 | +491.77 | 245 | 490 | `1dd2c04c7c1721617109bb3bef1d71e357f638792ae96bd48725cf6cb56a4e09` |

## 変更方針

- Scenarioへ`ea_id`と`artifact_prefix`を追加する。
- レポート名、set検証ラベル、ログをEA設定から生成する。
- required_set_valuesはEA固有の非空マッピングとし、具体的な必須キーは各シナリオJSONとテストで保証する。
- Smart Gold Hunter用8ファイル（4 set、4 JSON）とWave Rider用8ファイルを新規作成する。
- 各シナリオは3000 USDを先に実行し、Net Profit、Trades、deal数、deal日時・方向・価格を手動ログと照合する。
- Net Profit許容差は既承認の±0.5%または±0.10 USDを使用する。
- 3000 USDがPASSした場合だけ1000 USDを実行する。
- 手動HTMLがないPF、RF、Sharpe、Equity DDは実測値として保存する。
- 汎用set、QQ専用set、既存結果、Git設定は変更しない。
