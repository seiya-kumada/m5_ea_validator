# テスト結果

実施日: 2026-08-20

## 自動テスト

- コマンド: `uv run python -m unittest discover -s tests -v`
- Python: uv管理 CPython 3.13.12
- 結果: 24件成功、失敗0件
- 構文確認: `uv run python -m compileall -q mt5_ea_validator tests` 成功
- 実行前確認: MT5、EA、専用set、固定条件、対象MT5停止状態の確認に成功

主な検証範囲:

- QQ/WF1固定条件と専用set
- UTF-16 setの安全なステージングと既存ファイル上書き防止
- MT5起動INIおよびDeposit以外の同一性
- MT5プロセス検出とレポート成果物移動
- 英語／日本語HTML指標抽出
- 294 dealの日時・方向・価格ハッシュ
- Commission、Swap、deal Profit集計
- 新Safety Gateの許容差、PASS時の1500 USD続行、FAIL時の停止
- `minor accounting difference` の構造化記録

## 実MT5エンドツーエンドテスト

- Run ID: `20260820T164114584885_4be7de0b`
- MT5: TitanFX-MT5-Demo / Build 6090
- EA: Quantum Queen X MT5
- WF: WF1
- Period: 2025-07-01 ～ 2025-09-30
- MT5 return code: 3000 / 1500ともに0
- Campaign status: success

### Deposit 3000 USD

| 指標 | 自動結果 | 手動基準 | 判定 |
|---|---:|---:|---|
| Net Profit | 53.91 USD | 53.98 USD | PASS（差 -0.07 USD） |
| Trades | 147 | 147 | PASS |
| Profit Factor | 1.25 | 約1.26 | PASS |
| Recovery Factor | 0.54 | 約0.54 | PASS |
| Sharpe Ratio | 2.99 | 2.99 | PASS |
| Equity DD | 3.35% | 約3.35% | PASS |
| deal系列 | 294件 | 294件 | PASS（日時・方向・価格が完全一致） |

Net Profit差は `minor accounting difference` として記録した。自動結果の会計合計はCommission -105.84 USD、Swap -20.16 USD、deal Profit +179.91 USDで、合計はNet Profit +53.91 USDと一致する。

### Deposit 1500 USD

| 指標 | 結果 |
|---|---:|
| Net Profit | 53.91 USD |
| Return on initial deposit | 3.594% |
| Trades | 147 |
| Profit Factor | 1.25 |
| Recovery Factor | 0.54 |
| Sharpe Ratio | 2.94 |
| Equity DD | 6.69% |
| deal数 | 294 |

1500 USDと3000 USDの294 dealは、日時・方向・約定価格だけでなく、Commission、Swap、各deal Profitも1件ずつ完全一致した。固定LotのためNet Profitは同じで、初期資金半減に伴いEquity DDが約2倍となった。資金不足、証拠金不足、Stop Out、注文不能による取引系列の変化は確認されなかった。

## 会計差調査の制約

手動基準のTesterログには26個の決済後残高チェックポイントがあるが、Commission、Swap、各deal Profitを列として保持する手動HTMLレポートは存在しない。そのため、手動結果と自動結果の各会計列の直接比較はできない。

残高差は最初の決済群で -0.03 USD、2025-08-22に -0.04 USD、2025-09-25に -0.07 USDへ段階的に変化した。日時・方向・約定価格が全件一致しているため、取引系列の差ではなく、会計計算または端数処理の微差として分類する。詳細は実行フォルダの `accounting_comparison.md` に保存した。

## 1000 USD追加実行

- Run ID: `20260820T170151980640_faac5929`
- 実行順序: 3000 → 1500 → 1000 USD
- Campaign status: success
- 3000 USD Safety Gate: PASS

| Deposit | Net Profit | Return | Trades | PF | RF | Sharpe | Equity DD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 3000 USD | 53.91 USD | 1.797% | 147 | 1.25 | 0.54 | 2.99 | 3.35% |
| 1500 USD | 53.91 USD | 3.594% | 147 | 1.25 | 0.54 | 2.94 | 6.69% |
| 1000 USD | 53.91 USD | 5.391% | 147 | 1.25 | 0.54 | 2.89 | 10.03% |

1000 USDでも294 dealの日時・方向・価格・Commission・Swap・Profitは3000 USDと完全一致した。証拠金不足、Stop Out、資金不足による注文失敗はない。市場休止によるクローズ失敗が2回記録されているが、同じ日時・ポジションで3000、1500、1000 USDの全テストに発生しているため、資金水準による事象ではない。

## WF2～WF4追加実行

| WF | 期間 | Run ID | 3000 Gate | 1000実行 |
|---|---|---|---|---|
| WF2 | 2025-10-01～2025-12-31 | `20260820T173749281244_312c65ea` | PASS | success |
| WF3 | 2026-01-01～2026-03-31 | `20260820T174242150236_eaa589c5` | PASS | success |
| WF4 | 2026-04-01～2026-06-30 | `20260820T174511916304_102ba62e` | PASS | success |

WF2の3000 USDは手動Net Profit +213.27 USDと完全一致した。WF3は手動+397.15 USDに対して自動+397.10 USD（差-0.05 USD）、WF4は手動+362.09 USDに対して自動+362.06 USD（差-0.03 USD）で、いずれも `minor accounting difference` として許容範囲内。Trades、deal数、全dealの日時・方向・価格は各手動ログと完全一致した。

全WFで3000／1000 USD間の日時、方向、価格、Commission、Swap、各deal Profitは1件ずつ完全一致した。資金不足、証拠金不足、Stop Outはない。市場休止による注文・クローズ失敗は両Depositで同一に発生し、資金水準による事象ではない。
