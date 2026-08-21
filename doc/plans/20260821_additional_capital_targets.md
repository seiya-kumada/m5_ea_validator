# 追加Capital Stress実行計画

## 実施結果

- [x] `--target-deposit`を実装し、既存JSONを変更せず指定資金だけを追加実行
- [x] 34件の自動テスト、構文確認、差分検査に成功
- [x] 12シナリオのpreflightに成功
- [x] Wave Rider WF1〜WF4の1500 USDが正常終了
- [x] Quantum Queen WF1〜WF4の750 USDが正常終了
- [x] Smart Gold Hunter WF1〜WF4の750 USDが正常終了
- [x] 12件すべての3000 USD Safety GateがPASS
- [x] 全指定資金で3000 USDとdeal identity・会計合計が一致
- [x] 24実行の資金・証拠金・Stop Out・DD保護ログ監査を完了
- [x] 結果文書とdata配下サマリーを保存

作成日: 2026-08-21

## 対象

- Wave Rider WF1〜WF4: 1500 USD
- Quantum Queen WF1〜WF4: 750 USD
- Smart Gold Hunter WF1〜WF4: 750 USD

## 実施手順

1. 既存シナリオ、専用set、未コミット変更を確認する。
2. `--target-deposit`で3000 USD benchmarkと指定資金だけを実行できるようにする。
3. 正常値、0以下、benchmarkと同額、既存設定非変更を自動テストする。
4. uvで全自動テストと構文確認を実行する。
5. 全12シナリオでpreflightを実行する。
6. Wave Rider WF1〜WF4を3000→1500 USDで直列実行する。
7. Quantum Queen WF1〜WF4を3000→750 USDで直列実行する。
8. Smart Gold Hunter WF1〜WF4を3000→750 USDで直列実行する。
9. Net Profit、Trades、Deals、PF、RF、Sharpe、Equity DD、deal identityと資金関連ログを監査する。
10. 結果をdata配下とテスト結果文書へ保存する。

## 完了条件

- 12件すべてで3000 USD Safety GateがPASSする。
- 指定した12件の追加資金テストが正常終了する。
- 資金依存の取引系列変化と保護機能／証拠金関連イベントを記録する。
- 自動テスト、構文確認、差分検査が成功する。
