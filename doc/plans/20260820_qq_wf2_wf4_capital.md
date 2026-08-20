# QQ WF2～WF4 Capital Stress実装計画

作成日: 2026-08-20

## 対象

| WF | 期間 | 実行順序 |
|---|---|---|
| WF2 | 2025-10-01～2025-12-31 | 3000 → 1000 USD |
| WF3 | 2026-01-01～2026-03-31 | 3000 → 1000 USD |
| WF4 | 2026-04-01～2026-06-30 | 3000 → 1000 USD |

3000 USDを先にする理由は、手動No Delayログとの再現性を確認してからCapital Stressへ進むためである。

## 実装手順

1. ScenarioへWF識別子とWF別必須set値を追加し、WF1～WF4の期間対応を検証する。
2. BenchmarkのPF、RF、Sharpe、Equity DDを、手動基準がある場合だけ検証できる任意項目にする。
3. set検証、レポート名、マニフェスト、ログのWF1固定表現を設定駆動へ変更する。
4. MT5プロファイルを基に `QQ_WF2.set`、`QQ_WF3.set`、`QQ_WF4.set` を作成する。
5. WF2～WF4のシナリオJSONを作成し、手動ログの3000 USD基準を設定する。
6. 設定、期間、set分離、任意Benchmark、レポート名、ゲート動作の自動テストを追加・更新する。
7. uvで単体テスト、構文確認、各シナリオのpreflightを行う。
8. WF2、WF3、WF4を直列実行し、3000 USD PASS後に1000 USDを実行する。
9. 指標、deal監査、資金関連エラー、成果物の保存を確認し、結果を文書化する。

## 完了条件

- 全自動テストが成功する。
- 各WFの専用setが正しいWFO選択値を保持する。
- 3つの3000 USD Safety GateがPASSする。
- 3つの1000 USDテストが正常終了する。
- 各HTML、INI、result.json、run_manifest.json、execution.logが一意なdataフォルダへ保存される。

## 完了状況

- [x] WF別シナリオ、専用set、任意Benchmarkを実装
- [x] 24件の自動テスト、構文確認、3シナリオのpreflightに成功
- [x] 3つの専用setが各MT5プロファイルの49入力と完全一致
- [x] WF2の3000 USD Safety Gate PASS、1000 USD完走
- [x] WF3の3000 USD Safety Gate PASS、1000 USD完走
- [x] WF4の3000 USD Safety Gate PASS、1000 USD完走
- [x] 全WFで3000／1000 USDの各deal会計列が完全一致
