# テスト結果

## 追加Capital Stress（2026-08-21）

`--target-deposit`を使用し、Wave Riderは全WFを3000→1500 USD、Quantum QueenとSmart Gold Hunterは全WFを3000→750 USDで実行した。12件すべてで3000 USD Safety GateがPASSし、指定資金まで正常終了した。

### Wave Rider 1500 USD

| WF | Net Profit | Return | Trades | Deals | PF | RF | Sharpe | Equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| WF1 | +587.27 USD | +39.151% | 382 | 764 | 2.37 | 2.26 | 12.79 | 12.84% |
| WF2 | +603.59 USD | +40.239% | 461 | 922 | 2.00 | 2.58 | 13.77 | 12.42% |
| WF3 | +632.62 USD | +42.175% | 378 | 756 | 2.38 | 4.01 | 28.98 | 10.13% |
| WF4 | +491.77 USD | +32.785% | 245 | 490 | 2.56 | 0.89 | 7.81 | 28.78% |

全WFで1500 USDと3000 USDのdeal identity、Commission、Swap、deal Profitが一致した。WF4でもDD保護は発動しなかった。

### Quantum Queen 750 USD

| WF | Net Profit | Return | Trades | Deals | PF | RF | Sharpe | Equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| WF1 | +53.91 USD | +7.188% | 147 | 294 | 1.25 | 0.54 | 2.84 | 13.37% |
| WF2 | +213.27 USD | +28.436% | 199 | 398 | 2.04 | 5.16 | 30.01 | 5.46% |
| WF3 | +397.10 USD | +52.947% | 165 | 330 | 2.60 | 2.70 | 36.82 | 13.00% |
| WF4 | +362.06 USD | +48.275% | 181 | 362 | 3.04 | 3.40 | 39.77 | 13.04% |

全WFで750 USDと3000 USDのdeal identity、Commission、Swap、deal Profitが一致した。WF1、WF3、WF4の3000 USD基準差は従来どおり `minor accounting difference` として記録されている。

### Smart Gold Hunter 750 USD

| WF | Net Profit | Return | Trades | Deals | PF | RF | Sharpe | Equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| WF1 | +31.08 USD | +4.144% | 91 | 182 | 1.04 | 0.23 | 0.73 | 16.11% |
| WF2 | -113.27 USD | -15.103% | 90 | 180 | 0.87 | -0.40 | -3.92 | 33.47% |
| WF3 | +113.78 USD | +15.171% | 33 | 66 | 2.74 | 9.13 | 133.93 | 1.66% |
| WF4 | +74.37 USD | +9.916% | 27 | 54 | 2.40 | 4.07 | 113.01 | 2.39% |

全WFで750 USDと3000 USDのdeal identity、Commission、Swap、deal Profitが一致した。WF2は取引系列を維持したままEquity DDが33.47%まで上昇した。

今回の24実行ログでは、資金不足、No money、insufficient margin、Margin Call、Stop Out、DD保護発動はいずれも0件だった。

Run ID: Wave Rider WF1 `20260821T141332333183_9c20d307`、WF2 `20260821T141505366788_f5c38bbf`、WF3 `20260821T141708832682_c0c96d31`、WF4 `20260821T141953630807_0f91ffae`。QQ WF1 `20260821T142711082974_af519fec`、WF2 `20260821T142825105067_ebe8fefa`、WF3 `20260821T143010238143_fe23c5d5`、WF4 `20260821T143226719579_1f2a31cb`。Smart WF1 `20260821T143827351432_86675847`、WF2 `20260821T144055142637_99ef0258`、WF3 `20260821T144523496803_eddd022f`、WF4 `20260821T145141222968_6437d33f`。

自動テストは34件成功、構文確認、12シナリオpreflight、差分検査も成功した。

## Smart Gold Hunter / Wave Rider Capital Stress（2026-08-21）

実行条件はXAUUSD/H1、Every tick based on real ticks、No Delay、1:500、USD、固定Lot 0.01、3000 USD Safety Gate通過後に1000 USDを実行した。8シナリオすべてで3000 USD GateがPASSし、16実行すべてが正常終了した。

### Smart Gold Hunter

| WF | Deposit | Net Profit | Return | Trades | Deals | PF | RF | Sharpe | Equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| WF1 | 3000 | +31.08 USD | +1.036% | 91 | 182 | 1.04 | 0.23 | 0.75 | 4.33% |
| WF1 | 1000 | +31.08 USD | +3.108% | 91 | 182 | 1.04 | 0.23 | 0.74 | 12.37% |
| WF2 | 3000 | -113.27 USD | -3.776% | 90 | 180 | 0.87 | -0.40 | -3.81 | 9.16% |
| WF2 | 1000 | -113.27 USD | -11.327% | 90 | 180 | 0.87 | -0.40 | -3.89 | 25.85% |
| WF3 | 3000 | +113.78 USD | +3.793% | 33 | 66 | 2.74 | 9.13 | 136.16 | 0.42% |
| WF3 | 1000 | +113.78 USD | +11.378% | 33 | 66 | 2.74 | 9.13 | 134.68 | 1.25% |
| WF4 | 3000 | +74.37 USD | +2.479% | 27 | 54 | 2.40 | 4.07 | 114.67 | 0.61% |
| WF4 | 1000 | +74.37 USD | +7.437% | 27 | 54 | 2.40 | 4.07 | 113.55 | 1.80% |

全WFで3000/1000 USD間のdeal identity、Commission、Swap、deal Profitが一致した。

### Wave Rider

| WF | Deposit | Net Profit | Return | Trades | Deals | PF | RF | Sharpe | Equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| WF1 | 3000 | +587.27 USD | +19.576% | 382 | 764 | 2.37 | 2.26 | 12.71 | 7.37% |
| WF1 | 1000 | +587.27 USD | +58.727% | 382 | 764 | 2.37 | 2.26 | 12.84 | 17.06% |
| WF2 | 3000 | +603.59 USD | +20.120% | 461 | 922 | 2.00 | 2.58 | 13.95 | 6.92% |
| WF2 | 1000 | +603.59 USD | +60.359% | 461 | 922 | 2.00 | 2.58 | 13.62 | 16.90% |
| WF3 | 3000 | +632.62 USD | +21.087% | 378 | 756 | 2.38 | 4.01 | 29.68 | 5.16% |
| WF3 | 1000 | +632.62 USD | +63.262% | 378 | 756 | 2.38 | 4.01 | 28.27 | 14.94% |
| WF4 | 3000 | +491.77 USD | +16.392% | 245 | 490 | 2.56 | 0.89 | 8.10 | 16.13% |
| WF4 | 1000 | -39.37 USD | -3.937% | 250 | 500 | 0.95 | -0.07 | -1.79 | 41.33% |

WF1の3000 USDは手動+587.37 USDに対して自動+587.27 USD（-0.10 USD）だった。Trades 382、Deals 764、全deal identityが一致し、許容内の `minor accounting difference` としてCommission -275.04 USD、Swap -28.67 USD、deal Profit +890.98 USDを記録した。

WF1〜WF3は3000/1000 USD間で取引系列と会計合計が一致した。WF4の1000 USDだけは2026-06-22 04:00:06にDD保護が35.03%（閾値35.00%）で発動し、10ポジションの処理後に当日取引を停止したため系列が分岐した。全16実行で資金不足、証拠金不足、Margin Call、Stop Outは記録されなかった。

Run ID: SGH WF1 `20260821T102012239595_3f8295c1`、WF2 `20260821T102548136142_b01f9cfb`、WF3 `20260821T103815183442_813bdb02`、WF4 `20260821T105103120603_572efb46`。Wave Rider WF1 `20260821T110112888868_fa0b861d`、WF2 `20260821T110358376463_305934a5`、WF3 `20260821T110742301640_4f7e56ab`、WF4 `20260821T111225295683_f5ca3e5a`。

自動テストは `uv run python -m unittest discover -s tests -v` で31件成功、構文確認と8シナリオのpreflightも成功した。

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
