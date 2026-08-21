# Smart Gold Hunter / Wave Rider Capital Stress実装計画

## 実施結果（2026-08-21）

- [x] EA非依存のシナリオ／set／成果物命名へ一般化
- [x] 8専用setを正本Testerプロファイルの全入力と照合
- [x] 31件の自動テスト、構文確認、8シナリオpreflightに成功
- [x] 8つの3000 USD Safety GateがPASS
- [x] 8つの1000 USDテストが正常終了
- [x] 16実行の資金不足・証拠金不足・Stop Outログ監査を完了
- [x] Wave Rider WF4／1000 USDのDD保護発動と取引系列分岐を記録
- [x] EA別・WF別成果物と横断サマリーをdata配下へ保存

作成日: 2026-08-21

## 対象

- Smart Gold Hunter: WF1～WF4
- Wave Rider EA MT5: WF1～WF4
- 各シナリオの実行順序: 3000 → 1000 USD
- Model: Every tick based on real ticks
- Execution delay: No Delay
- Symbol / Timeframe: XAUUSD / H1
- Leverage / Currency: 1:500 / USD
- Optimization / Forward: OFF / OFF

## 実装手順

1. Scenario、レポート命名、set検証をEA設定駆動へ一般化する。
2. 既存QQの動作を維持する後方互換テストを更新する。
3. 8個のTesterプロファイルを基にEA別・WF別専用setを作成する。
4. 8個のシナリオJSONへ期間、set、3000 USD手動基準、3000→1000の順序を定義する。
5. EA別主要入力、全入力保持、成果物名、ゲート停止／続行を自動テストする。
6. uvで全テスト、構文確認、8専用setと正本プロファイルの全入力比較、8シナリオのpreflightを行う。
7. Smart Gold Hunter WF1～WF4を順番に実行する。
8. Wave Rider WF1～WF4を順番に実行する。
9. 3000／1000の各deal会計列、資金不足、証拠金不足、Stop Outを横断監査する。
10. 結果をdata配下のEA別・WF別フォルダと横断集計へ保存し、テスト結果文書を更新する。

## 完了条件

- 自動テストがすべて成功する。
- 8専用setの全入力が正本プロファイルと一致する。
- 8つの3000 USD Safety GateがPASSする。
- 8つの1000 USDテストが正常終了する。
- 各実行成果物がEA別・WF別の一意なdataフォルダへ保存される。
- Capital依存の取引系列変化と資金関連エラーの有無が記録される。
