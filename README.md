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

## Transaction Cost Stress 第1層

第1層は、元XAUUSDティックを次の式で加工したカスタムシンボルによる、不利約定価格・取引コストストレスです。純粋な約定スリッページテストではありません。

```text
Bid' = Bid - stress_points * Point
Ask' = Ask + stress_points * Point
```

EAと各WFのsetは変更せず、Deposit 3000 USD、Every tick based on real ticks、No Delayで比較します。最初にS=0のカスタムシンボルを生成し、既存XAUUSD基準をSafety Gateで再現できた場合だけ正のストレスへ進みます。

TitanFX MT5を終了してから、まずS=0を生成します。

```powershell
uv run python -m mt5_ea_validator build-cost-symbols `
  --config config\qq_wf1_capital.json `
  --stress-points 0
```

生成された`build_index.json`のPoint、Tick Size、元スプレッド分布を確認して正の水準を確定し、別の生成を追加できます。

```powershell
uv run python -m mt5_ea_validator build-cost-symbols `
  --config config\qq_wf1_capital.json `
  --stress-points 2 5 10
```

S=0と正の水準の`build_index.json`をすべて指定し、各EA/WFを直列実行します。`--config`には対象シナリオを列挙します。

```powershell
uv run python -m mt5_ea_validator run-cost-suite `
  --build-index <S0-build-index.json> <positive-build-index.json> `
  --config config\qq_wf1_capital.json config\qq_wf2_capital.json
```

生成監査とテスト結果は`data/transaction_cost_stress`配下へ保存され、Git管理されません。

途中で中断した実行は、同じbuild indexとconfigを指定したうえで、結果フォルダを
`--resume-run`へ渡すと再開できます。完了済みケースは検証後にスキップされ、
中断中だった一部出力は`*_interrupted_*`として退避されます。

```powershell
uv run python -m mt5_ea_validator run-cost-suite `
  --build-index <S0-build-index.json> <positive-build-index.json> `
  --config config\qq_wf1_capital.json config\qq_wf2_capital.json `
  --resume-run <data\transaction_cost_stress\results\run-id>
```

## スリッページ耐性検証 第2層

Quantum QueenのWF1～WF4について、元のWF専用setを変更せず、`InpSlippage`の現在値だけを上書きした派生setで許容偏差感応度を比較します。100 pointsを各実行モード内の基準として先に実行し、その後0・2・5・10 pointsへ進みます。

```powershell
uv run python -m mt5_ea_validator run-slippage-suite `
  --config config\qq_wf1_capital.json config\qq_wf2_capital.json `
           config\qq_wf3_capital.json config\qq_wf4_capital.json `
  --slippage-points 0 2 5 10 100 `
  --execution-mode 0
```

`--execution-mode`は、`0`がNo Delay、`188`が固定188ms、`-1`がランダム延滞です。中断時は`--resume-run <result-directory>`で未完了ケースだけ再開できます。生の成果物は`data/slippage_tolerance`配下へ保存され、Git管理されません。利用者が明示した最終報告書と関連画像だけは例外として追跡します。
