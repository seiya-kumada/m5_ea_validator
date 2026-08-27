# スリッページ耐性検証 第2層 実装計画

## 目的

第2層では、EAが注文要求へ設定する許容価格偏差の感応度を評価する。第1層のようにBid/Askティックを加工して不利価格を作る試験ではなく、通常のXAUUSD上でQuantum Queenの`InpSlippage`だけを変更し、注文成立、取引欠落、取引系列および主要指標の変化を確認する。

## 対象

- EA: Quantum Queenのみ
- WF: WF1～WF4（既存期間を維持）
- Deposit: 3,000 USD
- Symbol: 通常の`XAUUSD`
- Model: Every tick based on real ticks
- Execution delay: No Delay
- `InpSlippage`: 100（既存基準）、0、2、5、10 points
- その他のEA入力、期間、レバレッジ、通貨、モデルは既存のWF専用設定から変更しない。

Smart Gold HunterとWave Riderには、現在の専用setで許容偏差・Slippageに相当する公開入力がないため、第2層のパラメータ感応度試験は適用対象外として記録する。

## 実装方針

1. 既存の`Scenario`、set解析・検証、UTF-16配置、`MT5Executor`、HTML指標解析、deal監査を再利用する。
2. 専用setを直接変更せず、実行フォルダ内に`InpSlippage`だけを上書きした派生setを生成する。
3. 派生setは元setとの差分が`InpSlippage`の現在値だけであることを機械検証する。
4. 最初に`InpSlippage=100`をWF1～WF4で実行し、既存のDeposit 3,000 USD基準に対するSafety Gateを適用する。
5. 4つの基準がすべてPASSした場合のみ、`0 / 2 / 5 / 10`を実行する。
6. 各水準を100-point基準と比較し、Trades、deal件数、deal系列ハッシュ、Net Profit、PF、RF、Sharpe、Equity DDの差を保存する。
7. 結果、派生set、INI、HTML、JSON、実行ログは`data/slippage_tolerance`配下の一意な実行フォルダへ保存し、Git管理しない。

## 計算数

```text
1 EA × 4 WF × 5 InpSlippage水準 = 20バックテスト
```

## 判定の位置づけ

- `InpSlippage=100`は既存基準の再現ゲートである。
- 取引系列、Commission、deal Profitが過去基準と一致し、Net Profit差がSwap等の会計条件差だけで完全に説明できる場合は、`PASS_WITH_ACCOUNTING_DRIFT`として現在の100-point結果を内部比較基準に採用する。
- 100以外は合否ではなく、100からの感応度を測定する。
- 全水準が完全一致しても失敗とはしない。その場合は、No DelayのStrategy Tester環境では`InpSlippage`の効果を観測できなかったと結論づける。
- `InpSlippage`は強制的な約定差を注入する値ではない。したがって第2層の結果を実スリッページ分布とは解釈しない。

## テスト方針

- setの単一値上書きと非対象値保持の単体テスト
- 不正水準、QQ以外、基準値欠落、基準ゲート不一致の拒否テスト
- 基準を先に実行し、通過後だけ残りの水準へ進む順序テスト
- 結果JSONと100-point基準差分のテスト
- 全既存単体テスト

## 次の判断

No Delayで差が観測されない場合は、既存の固定188ms・ランダム延滞検証との組み合わせを第2層の追加試験として検討する。ただし、延滞との交絡を明示し、各延滞条件内で`InpSlippage`だけを比較する。

## 2026-08-27 追加試験

No Delayの20件で全指標とdeal系列が100-point基準へ完全一致したため、次を追加する。

- 固定188ms: 4 WF × 5水準 = 20件
- ランダム延滞: 4 WF × 5水準 = 20件
- ランダム延滞再現性確認: 100 points × 4 WFを独立再実行

ランダム延滞の独立再実行が一致しない場合、ランダム延滞内の水準差は参考値とし、`InpSlippage`の因果効果とは判定しない。
