# mt5-ea-validator

MetaTrader 5 Strategy Testerをコマンドラインから実行し、EAの検証結果を再現可能な形で保存するPythonツールです。

現在の対象はQuantum Queen X MT5のWF1 Capital Stress Testです。

## 開発コマンド

```powershell
uv sync
uv run python -m unittest discover -s tests -v
```

## 実行前確認

TitanFX MT5を終了してから、次を実行します。

```powershell
uv run python -m mt5_ea_validator run --config config\qq_wf1_capital.json
```

バックテスト結果は`data/backtest_results/qq_wf1_capital`配下の実行別フォルダへ保存されます。設定JSONの`deposits`を順番に実行し、最初の基準DepositがSafety Gateを通過した場合のみ後続Depositへ進みます。現在の順序は3000、1500、1000 USDです。
