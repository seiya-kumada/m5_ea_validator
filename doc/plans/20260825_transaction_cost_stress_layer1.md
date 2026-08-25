# Transaction Cost Stress 第1層 実装計画

日付: 2026-08-25

## 目的

Quantum Queen X MT5、Smart Gold Hunter、Wave Rider EA MT5について、WF1～WF4で使用したEA入力値を固定したまま、XAUUSDのBid/Askを不利な方向へ加工したときの耐性を共通条件で評価する。

この試験はMT5標準機能では直接指定できない「純粋な約定スリッページ」の注入ではない。EAから見えるBid/Askとスプレッドも変化するため、結果は **adverse execution-price proxy（不利約定価格・取引コストストレス）** として扱う。

## 2026-08-25 検証計画の追補: 永続化後ティック監査

Wave Rider/WF1のS=0で通常シンボルより5,094 ticks少ないことがTesterログから判明したため、次の順序へ変更する。

1. `CustomTicksReplace`直後に、日単位でカスタムシンボルを再読込する。
2. 書込み前の加工配列と、永続化後配列の件数および`time_msc`・Bid・Askハッシュを比較する。
3. read-back不一致は生成失敗とし、日付・入力件数・永続化件数をmanifestに残す。
4. read-back監査済みschema v2のS=0を新規生成する。schema v1は正のストレスに再利用しない。
5. QQ/WF1、Wave Rider/WF1、残り全EA/WFの順にS=0 Gateを再確認する。
6. 全S=0がPASSしてから、観測スプレッドに基づくS>0水準を確定する。

同一`time_msc`の複数ticksはMQL5公式仕様で許容されるため、監査結果を得る前にtick時刻や順序を変更しない。

### Tester入力系列の追補

保存後ティック監査は一致したが、通常XAUUSDでは実ティック欠損分をM1バーから補完していた。そこで、次を生成Gateへ追加する。

- 生成対象期間を2024-01-01～2026-07-01とし、最初のWFにも通常シンボル相当の事前履歴を与える。
- 実ティック登録後に、元XAUUSDのM1バーを同じ期間で複製する。
- M1バーの件数、時刻、OHLC、tick volume、spread、real volumeを保存後に再読込して監査する。
- 通常／カスタムのTesterログで、履歴開始日、実ティック欠損分の生成、最終処理tick数を比較する。

### S=0 Gate完了後の正ストレス追補

S=0は3 EA×4 WFの12ケースすべてで、Trades、deal件数、dealの日時・方向・約定価格ハッシュを含むSafety Gateを通過した。Wave Rider/WF1では通常XAUUSDと同じ24分の実ティック欠損生成と18,362,232 ticksも確認した。

正のストレスでは、実ティック欠損区間だけ無加工にならないよう、M1バーも次の式で加工する。

```text
M1 OHLC' = M1 OHLC - stress_points * Point
M1 spread' = M1 spread + 2 * stress_points
```

時刻、tick volume、real volumeは保持する。source・加工後・保存後のM1ハッシュを記録し、加工後と保存後の一致を生成Gateとする。

### 利用者中断後の再開計画

最終48ケースのうち11件完了時点で、利用者のMT5利用のためキャンペーンを中断した。再開前に次を実装・検証する。

1. 既存suite manifestと11件の`result.json`を読み、シナリオ・S水準・カスタムシンボル・成果物の整合性を確認する。
2. 完了済みS=0から基準指標とdeal監査を復元する。
3. `result.json`が存在する組合せは再実行せず、未完了組合せだけを元の順序で実行する。
4. 中断されたプロジェクト側部分フォルダとMT5データ領域の一時ステージングは削除せず、`interrupted`名へ退避する。
5. 再開・部分フォルダ退避・完了済みスキップをユニットテストしてから実MT5を再開する。

## 変更しない条件

- EA本体と各WF専用set
- WF期間
- H1、Every tick based on real ticks
- No Delay
- 1:500、USD
- Optimization OFF、Forward OFF
- 固定Lotその他、各WFで選択済みの全EA入力値
- 既存Capital Stressの設定、実行経路、成果物

第1層の比較用Depositは、全EA・全WFで既存の基準が揃っている3000 USDとする。

## ティック加工

元シンボルをTitanFXの`XAUUSD`とし、カスタムシンボルごとに全ティックを次の式で加工する。

```text
Bid' = Bid - stress_points * Point
Ask' = Ask + stress_points * Point
```

- `stress_points=0`は生成経路そのものの再現確認に使う。
- `stress_points>0`では売買の片道価格を同じポイント数だけ不利にする。
- 元の`time_msc`、Last、Volume、flagsは保持する。
- 加工後価格は元シンボルのDigitsへ正規化する。
- `Bid'>0`かつ`Ask'>=Bid'`を必須とする。元データに存在するゼロスプレッドはS=0で保持し、負スプレッドは拒否する。

ストレス水準は片道0、2、5、10 pointsとする。実測した元スプレッドは中央値17、p95 26、p99 43 pointsであり、正の水準は総スプレッドへ4、10、20 pointsを加える。初期候補10、30、50 pointsから変更した理由は方針変更履歴へ記録した。

## 実装構成

1. MQL5スクリプトで元ティックを`CopyTicksRange`により日単位で取得する。
2. カスタムシンボルをXAUUSDの銘柄仕様から作成し、加工ティックを`CustomTicksReplace`で日単位に登録する。
3. Python側はスクリプトの配置、MetaEditorによるコンパイル、MT5 `/config`の`[StartUp] Script`実行、完了マニフェスト検証を担当する。
4. Python側にTransaction Cost Stress専用設定と実行経路を追加し、既存Capital Stressの`symbol == XAUUSD`制約は維持する。
5. 生成記録には元シンボル、カスタムシンボル、期間、Point、Digits、Tick Size、stress points、ティック件数、先頭・末尾時刻、ティック監査ハッシュを保存する。
6. テスト結果は`data/transaction_cost_stress`配下の一意な実行フォルダへ保存し、Git管理しない。

## Safety Gate

### 生成ゲート

- MQL5スクリプトの終了状態が成功であること。
- 対象期間のティック件数が0より大きいこと。
- 入力件数と登録件数が一致すること。
- 日付順序、価格条件、設定したstress pointsが監査記録と一致すること。

### S=0再現ゲート

各EA/WFについて、同じ3000 USDの既存XAUUSD基準と比較する。

- Trades: 完全一致
- dealの日時・方向・約定価格: 完全一致
- Net Profit: 基準値に対し±0.5%または±0.10 USD以内
- Equity DD: 基準値がある場合は±0.01 percentage point程度
- PF/RF/Sharpe: 基準値がある場合はレポート丸め誤差程度

S=0が失敗したEA/WFでは、その後のストレス水準を実行せず、カスタムシンボル名依存、銘柄仕様差、ティック欠落などを調査する。

### S>0評価

S>0は元のdeal系列との一致を合否条件にしない。内部ロジックの反応も含め、次を基準比として記録する。

- Net Profit、Return、PF、RF、Sharpe、Equity DD
- Trades、Deals、deal系列ハッシュ
- 取引減少・消失、資金不足、注文拒否、DD保護作動の兆候
- 1 trade当たり損益、および可能な場合は総取引量当たりの損益悪化

## テスト順序

1. Python単体テストとMQL5コンパイル
2. XAUUSDの銘柄仕様・元スプレッド分布取得
3. 全WFを含む期間のS=0カスタムシンボル生成
4. まずQQ/WF1/3000 USDでS=0ゲート
5. ゲート成功後、残り11 EA/WFのS=0ゲート
6. 全S=0結果を確認後、確定したS>0水準を小さい順に直列実行

## 中止条件

- 対象TitanFX MT5が起動中
- 元ティックが同期できない、または期間に欠落がある
- コンパイルまたはカスタムシンボル生成マニフェストが不正
- S=0が既存基準を再現しない
- 既存ファイルを上書きする可能性がある
