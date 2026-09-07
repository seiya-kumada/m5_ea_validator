# UBS 約定遅延耐性の実装・実行計画

## 目的と条件

2026-09-07: Phase 3報告書の次段階に従い、xau_h1_c5 (0.03 lot) と daily_l (0.04 lot) を先行対象にする。各WFの期間、Deposit 3000 USD、H1、実ティック、レバレッジ500、選択済みsetを維持する。ExecutionModeのみ0、188、-1へ変える。ランダムは各WF・戦略で3回の初期評価とし、独立乱数seedの制御は主張しない。2戦略×4WF×(基準1+固定1+ランダム3)=40件。

## 実装順序

1. 保存済み四半期結果と選択setの由来・ハッシュ・条件を検証する。
2. 既存execute_ubs_case、固定lot監査、ティック監査、BelowNormal/4CPU実行を共用する専用ランナーを追加する。
3. No Delay再現ゲート（Trades・deal系列一致、NP差 max(0.10 USD, 0.5%)、DD差0.01pp）を通過してから各WFの遅延ケースへ進む。
4. ケース別の保存・ハッシュ付き再開・ケース間停止を実装し、模擬実行テストと既存テストを実施する。
5. MT5停止を確認して直列実行する。各ケース完了時に結果と差分を保存する。

## 保存と評価

data/ubs_strategy_comparison/execution_delay/<run_id>/ 配下へmanifest、入力set、HTML、INI、ログ、result、集計CSVを保存する（Git管理外）。利益・DD・Trades・deal系列一致・Commission・Swapを基準と比較する。DD7.5%超は警告として保存する。ランダム反復は実測値を全て残し、少数回の結果を確率分布の確定値と解釈しない。

## 履歴

- 2026-09-07: ユーザーの開始指示により計画開始。前段階はNo Delay専用なので、既存安全条件を弱めず、遅延比較用の薄いランナーを追加する。共有コードを優先する。既存2報告書の利用者編集を保持する。
- 2026-09-07: 専用ランナーを追加し、模擬実行による順序・再現ゲート・ケース数制限・再開・保存結果改変検出を確認。全108テストPASS。run `20260907T110008682379_c686a0c1` を開始した。MT5およびmetatester64の両方でBelowNormal、ProcessorAffinity=15（4論理CPU）を実測確認した。

## 実行・再開方法

### 2026-09-07 MT5自動更新による再測定

初回runは7件保存後、8件目起動時のLiveUpdateで監視対象terminal64が終了しHTML未取得となった。Terminalログには11:05:51の更新起動と11:06:32のbuild 6182起動が記録され、旧7件はbuild 6140だった。再起動後の8件目は別プロセスで完走したが、CPU制限継承と収集監査が保証できないため採用しない。バージョン混在を避け、旧runとステージ成果物を保持して新runで40件を再測定する。新runでもNo Delayを前段階と比較し、再現ゲート通過を必須とする。

```powershell
uv run python -m mt5_ea_validator.ubs_execution_delay --baseline-run data\ubs_strategy_comparison\risk_sensitivity\quarterly_real_ticks\20260904T142905226930_1d391678
```

再開時は同じコマンドへ `--resume-run data\ubs_strategy_comparison\execution_delay\20260907T110008682379_c686a0c1` を追加する。`--max-cases N` は今回起動で新たに実行する件数を制限する。run直下へ `STOP_AFTER_CASE` ファイルを置くと現在ケース完了後に停止する。再開にはこの停止要求ファイルを取り除く必要がある。各ケースの `cumulative_summary.csv` は、その時点の累積集計スナップショットである。
