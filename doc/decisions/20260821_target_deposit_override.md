# Target Deposit指定実行の追加

日付: 2026-08-21

## 変更理由

既存シナリオJSONには過去に実行した1000 USDや1500 USDが含まれる。新しい資金額だけを検証するためにJSONへ値を追加すると、既存の全depositも再実行され、計算時間と成果物が不要に増える。一方、JSONを一時的に書き換えて戻す方式は再現性と監査性を損なう。

## 変更内容

`run`コマンドへ `--target-deposit` を追加する。指定時は設定JSONを変更せず、実行時のdeposit列を `3000 USD benchmark → target deposit` に置き換える。3000 USD Safety GateがPASSした場合のみtarget depositを実行する既存制御は維持する。

target depositは正の整数かつbenchmark depositと異なる値に限定する。実際に要求・完了したdepositは従来どおりrun manifestへ保存する。

## 非変更事項

- EA/WF専用set、期間、モデル、No Delay、レバレッジ、基準値は変更しない。
- 既存シナリオJSONのdeposit列は変更しない。
- Safety Gateの判定条件は変更しない。
