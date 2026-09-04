# UBS同一リスク比較・四半期安全確認報告書 作成計画

## 目的

完了済みのUBS Gold 7戦略について、約1年の最大Equity DDを5.0%へ近づける固定ロット選択と、同じロットによるWF1～WF4の実ティック安全確認を、目的・結論・詳細の順で再現可能な総合報告書へまとめる。

## 使用する正本

- 原設定の約1年実ティック比較: `data/ubs_strategy_comparison/model_comparison_1y/20260902T162029093739_4e2ec09f/`
- 初回年間候補: `data/ubs_strategy_comparison/risk_sensitivity/annual_candidates/20260904T111925023840_653d6f4a/`
- 年間追加候補と最終選択: `data/ubs_strategy_comparison/risk_sensitivity/annual_adjustments/20260904T132413597926_c84df372/`
- 四半期実ティック安全確認: `data/ubs_strategy_comparison/risk_sensitivity/quarterly_real_ticks/20260904T142905226930_1d391678/`
- 同一リスク基準と選択ルール: `doc/decisions/20260903_ubs_same_risk_criterion.md`、`doc/decisions/20260904_ubs_same_risk_initial_lots.md`、`doc/decisions/20260904_ubs_same_risk_adjustments.md`

## 実装方針

1. 既存のUBS報告書生成コードを共有し、保存済みJSON・CSVから専用報告書を再生成できる関数とCLIを追加する。
2. 最終選択7件、選択元の年間結果7件、四半期結果28件、入力互換性、ティック品質、entry lot監査の完全性を生成前に検証する。
3. 報告書は`doc/reports/ubs_same_risk_comparison.md`へ、関連SVGは`doc/reports/assets/ubs_same_risk_comparison/`へ保存する。
4. 次の4図を作成する。
   - 約1年Equity DDと目標帯4.5～5.5%
   - 約1年Net ProfitとEquity DDのリスク・リターン配置
   - WF1～WF4のNet Profit
   - WF1～WF4のEquity DDと7.5%警戒線
5. 原設定との比較、固定ロット選択の制約、四半期ごとの収益・DD、ティック欠損許容、独立開始した四半期合計の限界を明記する。
6. `goldtradepro_a`は最低ロットでも年間DD 10.86%であり、他6戦略と同一リスクになっていないことを順位解釈から分離する。

## テスト方針

- 不完全または失敗した最終選択・四半期manifestを拒否する。
- 選択ロットと年間／四半期entry volumeの不一致を拒否する。
- 生成Markdownが目的・結論・詳細の順で、7戦略・4期間・4画像を含むことを確認する。
- SVGをXMLとして解析し、title、desc、明暗テーマ、大きな文字、DD目標帯・警戒線を確認する。
- `uv run python -m unittest discover -s tests -v`を実行する。
- 実データ生成後、28/28件、警告1件、主要数値、画像リンク、Git差分を監査する。

## 変更履歴

- 2026-09-04: Phase 3の同一リスク比較と四半期安全確認が完了したため、Phase 0～2の暫定報告書とは分けた専用報告書として開始した。`goldtradepro_a`は最低ロット制約でリスクを揃えられなかったため、7戦略を同条件と誤解させない表示・結論にする。
- 2026-09-04: 保存済み正本の完全性ゲート、Markdown、明暗テーマ対応SVG 4枚、再生成CLIを実装した。対象テスト4件と全106件がPASSし、実データから報告書を生成した。4画像をPNGへ一時レンダリングして、文字切れ、重なり、期間表示、目標帯、警告セルを目視確認した。
