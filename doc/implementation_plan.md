# MT5 EA Validator 実装計画

作成日: 2026-08-20  
完了日: 2026-08-20

## 目的

Quantum Queen X MT5（QQ）のWF1について、Deposit以外の条件を固定したCapital Stress TestをMT5 Strategy Testerで直列自動実行する。最初に3000 USDの再現性をSafety Gateで確認し、PASSした場合だけ1500 USD、1000 USDを順番に実行する。

## 固定条件

- EA: `Market\Quantum Queen X MT5.ex5`
- Symbol / Timeframe: XAUUSD / H1
- Period: 2025-07-01 ～ 2025-09-30
- Model: Every tick based on real ticks
- Execution delay: No Delay
- Leverage / Currency: 1:500 / USD
- Optimization / Forward: OFF / OFF
- Lot: 0.01 fixed
- `InpOrdersMax=10`
- `InpUseNfpFridayFilter=true`
- `InpTradingFridayNight=false`
- `InpDDMode=0`
- `InpDDValue=0`
- その他の入力値は、手動結果 +53.98 USDを再現したWF1 Testerプロファイルを保持する。

汎用setは変更せず、QQ/WF1専用の `config/sets/QQ_WF1.set` を使用する。

## Safety Gate

Deposit 3000 USDで次をすべて確認する。

- Trades: 147と完全一致
- dealの日時・方向・約定価格: 手動基準294件と完全一致
- Net Profit: 53.98 USDに対し、±0.5%または±0.10 USD以内
- Equity DD: 3.35%に対し±0.01 percentage point
- PF / RF / Sharpe: レポート丸め誤差の範囲

許容範囲内のNet Profit差は `minor accounting difference` として、Commission、Swap、deal Profit合計とともに記録し、後続Depositの実行を妨げない。

## 実装手順

1. uv管理のPythonプロジェクト、シナリオJSON、QQ/WF1専用setを作成する。
2. MT5起動INIを生成し、対象MT5が停止中であることを確認する。
3. MT5を `/config` で起動し、HTMLレポートを一意な `data/backtest_results` 配下へ保存する。
4. 3000 USDレポートから指標、deal系列、会計合計を抽出してSafety Gateを評価する。
5. PASS時のみDepositだけを1500 USD、1000 USDへ変更して直列実行する。
6. 実行条件、時刻、結果、レポート、判定、会計監査をJSONとログへ保存する。
7. 単体テスト、構文確認、実MT5エンドツーエンドテストを行う。

## 完了状況

- [x] uvプロジェクトと設定を実装
- [x] 専用setを作成し、汎用setを未変更のまま保持
- [x] MT5自動起動、直列実行、レポート保存を実装
- [x] 複合Safety Gateと会計監査を実装
- [x] 18件の単体テストと構文確認に成功
- [x] 3000 USDのSafety GateがPASS
- [x] 1500 USD Capital Stress Testが正常完了
- [x] 1000 USD Capital Stress Testを実行し、結果を記録

実行成果物は `data/backtest_results/qq_wf1_capital/<run-id>/` に保存し、Git管理対象とはしない。現段階ではGitの初期化・設定を行わない。
