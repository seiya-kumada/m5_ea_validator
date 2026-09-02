# UBS Gold 7戦略比較 実装・検証計画

## 目的

Ultimate Breakout System（UBS）のXAUUSD用set 7個を、再現可能で実運用に近い共通条件の下で比較する。
各段階の入力、MT5レポート、Testerログ、集計値、判定根拠を保存し、最終的に目的・結論・詳細の順で表とグラフを含む総合報告書へまとめる。

## 対象set

1. `9637052_basa_xauusd h1 - sr - scalp - 2010-2020 - 9.set`
2. `991005_SPD_UBS-XAUUSD-H1-C5.set`
3. `dailyL.set`
4. `E.set`
5. `GOLDTRADEPRO_a.set`
6. `MT5_longterm_E.set`
7. `MT5_longterm_J.set`

原本は `Sets_v6_faithful14/Sets_v6_faithful14/` に置き、変更しない。

## 成果物と保存方針

- 生データ: `data/ubs_strategy_comparison/`
- set目録・ハッシュ・抽出設定: `data/ubs_strategy_comparison/inventory/`
- 実行結果: `data/ubs_strategy_comparison/runs/<run_id>/`
- 段階別集計: `data/ubs_strategy_comparison/summaries/`
- 最終報告書: `doc/reports/ubs_gold_strategy_comparison.md`
- 報告書用画像: `doc/reports/assets/ubs_gold_strategy_comparison/`

`data/`以下はGit管理外とし、各runには少なくともシナリオ、EA/set識別情報、MT5設定、HTMLレポート、Testerログ、解析結果、実行状態を残す。

## 実施順序

### Phase 0: 入力と互換性の確定

- [x] 7 setのSHA-256、設定値、作成日時を目録化する。
- [x] インストール済みUBSデモ版のバージョンとファイル識別情報を記録する。
- [x] UBS v7.5でv6系setを読み込めるか確認する。
- [x] 各setの正しいTester銘柄・時間足を、set、付属資料、MT5表示、公式説明から確定する。
- [x] 資金管理、ニュース、GMT、スプレッド、最大保有数など比較に影響する主要設定を表にする。

初回目録: `data/ubs_strategy_comparison/inventory/20260902T005739271582_3662c8c7/`

確定条件: 7 setすべて`XAUUSD / H1`。EA内部の各時間足設定は原set値を使用する。

時間足が確定できないsetを推測だけで本試験へ進めない。`ST1_Timeframe`は内部計算時間足の可能性があるため、Tester時間足の根拠として単独使用しない。

### Phase 1: スモークテスト

- [x] 7 setを短期間、XAUUSD、原set、実ティック、No Delayで個別実行する。
- [x] 初期化成功、取引発生、設定読込警告、Testerエラー、レポート生成を確認する。
- [x] 失敗なし。今後失敗した場合は原因と方針変更理由を決定履歴へ記録してから修正する。

初回run（実ティック同期不足の参考証拠）:
`data/ubs_strategy_comparison/smoke/20260902T130931213191_640f76c3/`

同期後の正式な基準run:
`data/ubs_strategy_comparison/smoke/20260902T143126585336_b4e7cdd1/`

全7戦略がoperational compatibility gateを通過した。共通入力の値不一致は0件。ただしv7.5で追加された既定入力があるため、exact v6 result fidelityは未確認とする。

### Phase 1.5: v7.5基準値と実効入力の固定

- [x] `ExpertParameters`なしのデフォルト対照テストを実行する。
- [x] 旧setに存在しない入力と対照入力を比較する。
- [x] 7件の基準レポートから、原setを保持したv7.5忠実拡張setを作成する。
- [x] 原setと忠実拡張setのSHA-256、由来run ID、入力数を記録する。
- [x] 忠実拡張setを再読込し、表示219入力、主要成績、Trades、deal系列、会計合計が作成元基準と一致することを確認する。

Phase 1.5成果物:

- set未指定対照: `data/ubs_strategy_comparison/default_control/20260902T144355329754_a13d1609/`
- 忠実拡張set: `data/ubs_strategy_comparison/faithful_augmented_sets/20260902T143126585336_b4e7cdd1/`
- 最終再現検証: `data/ubs_strategy_comparison/faithful_validation/20260902T144639575189_3b69c558/`

注: 219表示入力だけで構成した派生setとその検証runも診断証拠として保持する。ただし初回基準には8月28日の実ティック同期不足があったため、正式な比較基準には使用しない。

### Phase 2: 原set忠実比較

- [x] 実ティックのウォームアップ／基準run直後にTesterログを解析するpreflightデータ品質ゲートを実装する。
- [x] 同期開始・終了日、要求終了日、欠損警告、判定理由をrun成果物へ保存する。
- [x] ゲートがFAILした場合は後続比較を開始しない。
- [ ] 原setを変更せず、実運用口座に合わせた通貨・入金・レバレッジ・Commission・Swap条件で比較する。
- [ ] 長期期間は`1 minute OHLC`で実行し、年別・月別・全期間の指標を保存する。
- [ ] 直近1年は`Every tick based on real ticks`でも実行する。
- [ ] 直近1年の`1 minute OHLC`と実ティック結果を比較し、モデル差を保存する。
- [x] 直近1年の2方式比較を14ケースとして実行・再開できるランナーと比較集計を実装する。
- [x] 既存のWF1～WF4相当期間を7 setすべてで実行する（7×4＝28件）。

期間:

- WF1: 2025-07-01～2025-09-30
- WF2: 2025-10-01～2025-12-31
- WF3: 2026-01-01～2026-03-31
- WF4: 2026-04-01～2026-06-30

この28件は再最適化を伴わないため、新規WFOではなく四半期別安定性比較として扱う。

四半期別比較run:
`data/ubs_strategy_comparison/quarterly_1m_ohlc/20260902T155309727908_b95bac15/`

- 28/28ケース成功
- 忠実拡張setの表示入力互換性FAIL 0件
- HTMLレポート欠損 0件
- 各四半期をDeposit 3,000 USDから独立に開始した。4四半期Net Profit合計は連続運用や複利運用の成績ではなく、四半期安定性を比較する参考集計として扱う。

各set固有の最適化・フォワード期間を証明する資料が残っていないため、すべての歴史検証を厳密なOut-of-Sample成績ではなく「過去データでの再検証」として扱う。

直近1年モデリング方式比較run（2026-09-02時点でユーザー指定により中断）:
`data/ubs_strategy_comparison/model_comparison_1y/20260902T162029093739_4e2ec09f/`

- 12/14ケース完了
- `1 minute OHLC`: 7/7完了
- `Every tick based on real ticks`: 5/7完了
- 残り: `mt5_longterm_e`、`mt5_longterm_j`
- 再開実行は`BelowNormal`、12論理CPU中2個のaffinityで負荷を制限する。

### Phase 3: 同一リスク比較

- [ ] 原setを保存し、資金管理項目だけを変更した派生setを別名で作る。
- [ ] UBSの資金管理パラメータの意味を資料または実測で確認してから正規化方式を決定する。
- [ ] 全戦略を同一の事前リスク基準で再実行し、原set順位との差を比較する。

### Phase 4: 実行・コスト耐性

- [ ] No Delayを再現基準とする。
- [ ] Fixed Delay 188msを実行する。
- [ ] Random Delayは複数回実行し、単一結果ではなく分布で評価する。
- [ ] 必要に応じてスプレッド、Commission、Swap、価格不利方向のストレスを行う。

### Phase 5: 資金耐性とポートフォリオ候補

- [ ] 実運用予定資金と低資金ケースで、証拠金不足、DD保護、取引系列分岐を確認する。
- [ ] 日次または週次損益の相関を計算し、低相関な組合せを候補化する。
- [ ] 単体成績とポートフォリオ成績を分けて評価する。

### Phase 6: 総合報告書

- [ ] 目的、結論、詳細の順で作成する。
- [ ] Net Profit、PF、RF、Sharpe、Equity DD、取引数、最大連敗、停滞期間、期間安定性、ストレス劣化率を表とグラフで示す。
- [ ] 使用したEA/setの識別情報、全テスト条件、制約、再現手順、保存先を明記する。

## 実装方針

- 既存の設定駆動型MT5実行、レポート保存、解析コードを可能な限り共有する。
- UBS固有条件は設定または小さなアダプターへ閉じ込め、既存EA向け処理を壊さない。
- Pythonはuvで管理し、`uv run`で実行する。
- 実装変更ごとに自動テストを追加または更新し、全テストを実行する。
- MT5の実計算前に設定生成・set読込・出力先・重複実行防止を自動テストする。
- 原set、既存結果、利用者の手修正済み報告書を上書きしない。

## 完了条件

- 各実行をrun IDから入力set、EA、Tester条件、HTML、ログ、解析結果まで追跡できる。
- 7戦略を原set忠実条件と同一リスク条件の両方で比較できる。
- 実行・コスト・資金耐性の影響を基準結果との差として説明できる。
- 最終報告書の数値とグラフを保存済み成果物から再生成できる。
