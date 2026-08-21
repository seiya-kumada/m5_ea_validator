# mt5-ea-validator

MetaTrader 5 Strategy Testerをコマンドラインから実行し、EAの検証結果を再現可能な形で保存するPythonツールです。

Quantum Queen X MT5、Smart Gold Hunter、Wave Rider EA MT5のWF1〜WF4 Capital Stress Testに対応しています。

## 開発コマンド

```powershell
uv sync
uv run python -m unittest discover -s tests -v
```

## 実行前確認

TitanFX MT5を終了してから、次を実行します。

```powershell
uv run python -m mt5_ea_validator preflight --config config\smart_gold_hunter_wf1_capital.json
uv run python -m mt5_ea_validator run --config config\smart_gold_hunter_wf1_capital.json
uv run python -m mt5_ea_validator preflight --config config\wave_rider_wf1_capital.json
uv run python -m mt5_ea_validator run --config config\wave_rider_wf1_capital.json
uv run python -m mt5_ea_validator run --config config\qq_wf1_capital.json
uv run python -m mt5_ea_validator run --config config\qq_wf1_capital.json --target-deposit 750
```

バックテスト結果は`data/backtest_results/{scenario_id}`配下の実行別フォルダへ保存されます。設定JSONの`deposits`を順番に実行し、最初の基準DepositがSafety Gateを通過した場合のみ後続Depositへ進みます。QQ/WF1は3000、1500、1000 USD、今回追加したSmart Gold HunterとWave Riderは3000、1000 USDの順です。

`--target-deposit`を指定すると、設定JSONを変更せずに3000 USD Safety Gateと指定資金だけを実行できます。
