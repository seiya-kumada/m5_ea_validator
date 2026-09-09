# UBS残り5戦略の約定遅延耐性

## 方針・実装計画

2026-09-09のユーザー指示により、xau_sr_scalp_h1、e、goldtradepro_a、mt5_longterm_e、mt5_longterm_jを追加検証する。既存選択lotは順に0.03、0.03、0.01、0.01、0.02。Deposit3000 USD、実ティック、H1、既存4WF、遅延0/188/-1（ランダム3回）で100件。DD未整合の3戦略は各戦略自身の遅延なしとの比較を中心に評価する。

1. 既存ランナーへ対象strategy_idを指定する引数を追加する。既定2戦略と既存報告書の互換性を維持する。
2. 空・重複・未知戦略を拒否し、指定順とケース数をテストする。全自動テストを実行する。
3. 新runに保存し、No Delay再現ゲート・入力・lot・ティック監査を共用する。BelowNormal/4CPUで直列実行し完了まで監視する。
4. ケースごとの保存と再開を維持する。実行中ケースと更新時刻もmanifestへ記録し、進捗を読み取りやすくする。

## 変更理由・履歴

- 2026-09-09: 先行2戦略の好成績は残り5戦略の遅延耐性を保証しないため、ユーザー指示により追加する。既存コードを複製せず対象選択を引数化する。前回の40件と報告書は保持する。
- 2026-09-09: 全111自動テストPASS後、run `20260909T093437057969_12b0fa2c` を開始。利用者の一時停止指示によりSTOP_AFTER_CASEを設定し、63/100件保存後に正常停止した。WF1・WF2各25件、WF3の13件が完了。最終ケースはWF3_goldtradepro_a_random_1（NP407.30 USD）。次はWF3_goldtradepro_a_random_2で、残り37件。MT5およびテスターは終了した。

## 再開

最新状態（2026-09-09 14:32 JST）：100/100件SUCCESS。全5戦略各20件が完了し、全result SHA-256、入力・ティック監査、HTML/INI存在、ケース順・重複なし、No Delay再現ゲート20/20を確認した。MT5およびテスターは終了。STOP_AFTER_CASEは解除済み。再開の必要はない。

98件での利用者指示による一時停止後、再開時に手動起動中のMT5を検出したため待機。利用者による終了後に99・100件目だけ実行した。最終ケースの累積集計は `data/ubs_strategy_comparison/execution_delay/20260909T093437057969_12b0fa2c/WF4_mt5_longterm_j_random_3/cumulative_summary.csv` に保存。

DD7.5%超の警告は5件、最大DD13.28%。純損益が負のケースはWF4_mt5_longterm_eのNo Delay(-0.02)、188ms(-21.76)、Random2(-12.91)、Random3(-20.11)の4件。これらはテスト実行の失敗ではなく、比較対象の実測結果として保持する。

63件から再開後、70件を保存した時点で、次ケース開始前のMT5プロセス確認PowerShellが15秒タイムアウトし停止した。監視ツール接続のタイムアウトも同時期に発生した。接続復旧後にMT5停止を確認し、利用者の続行指示により71件目から再開した。既存結果と判定基準の変更はない。その後98件目まで正常保存し、ケース境界で停止した。

明示的な再開指示を受けてrun直下のSTOP_AFTER_CASEを除去後、以下を実行する。完了済みresultのSHA-256を検証し、63件は再実行しない。

```powershell
uv run python -m mt5_ea_validator.ubs_execution_delay --baseline-run data\ubs_strategy_comparison\risk_sensitivity\quarterly_real_ticks\20260904T142905226930_1d391678 --strategies xau_sr_scalp_h1 e goldtradepro_a mt5_longterm_e mt5_longterm_j --resume-run data\ubs_strategy_comparison\execution_delay\20260909T093437057969_12b0fa2c
```
