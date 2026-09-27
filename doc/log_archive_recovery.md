# ログバックアップの復元手順と削除前確認

## 保存対象

2026-09-27の原本ログ957件（7,092,684,149 bytes）。
復元用の基点は `data/log_archives/20260927_all_logs/`。
`archive/manifest.json` と `archive/objects/` は必ず一組で保持する。
復元用ZIPにはこれらに加え、検証記録・復元コード・Python要件・本手順を含める。
HTML、set、結果JSON、グラフなどログ以外の原データを包括的にバックアップするZIPではない。

## 検証結果の注意

原本と復元957件のバイト内容は一致。再監査は370件が自動一致、旧形式1件は追加確認で一致。
初回 `verification_result.json` のstatusは旧形式エラーによりfailのまま残している。
`legacy_audit_supplement.json` と必ず併読する。元の品質不合格テストを合格にしたものではない。
残り586件は隣接する監査JSONなし等により意味的再監査対象外だが、バイト一致は検証済み。

## 別媒体バックアップ

ZIPと別記のSHA-256を外付けドライブ等へ保存する。コピー後、その保存先のZIPで
`Get-FileHash -Algorithm SHA256 -LiteralPath '<保存先ZIP>'` を実行し、記録値と一致することを確認する。
同一Cドライブ内の別フォルダだけではディスク故障対策にならない。
オンライン同期先の場合も同期完了とダウンロード後の一致を確認する。

## 復元（原本に上書きしない）

1. ZIPを新しい作業フォルダへ展開する。ZIP内の `archive/manifest.json` 等が見えるフォルダを基点とする。
2. Python 3.13とuvを用意する。ZIPに同梱するpyproject.toml/uv.lockはプロジェクトのPython要件を記録する。
3. 基点をPowerShellの作業フォルダにし、以下を実行する。復元先 `recovered_logs` は未作成であること。

```powershell
@'
from pathlib import Path
from mt5_ea_validator.log_archive import restore_archive
manifest = restore_archive(Path('archive'), Path('recovered_logs'))
print('Restored and SHA-256 verified:', len(manifest['files']))
'@ | uv run --no-project --python 3.13 python -
```

外部Python依存ライブラリは不要。復元処理は圧縮本体と復元した全ファイルの長さ・ハッシュを検証する。
既存の復元先がある場合は停止する。復元には約7.1 GB＋余裕の空き容量が必要。
ACLや元の更新時刻は復元せず、バイト内容とdata相対のフォルダ構造を復元する。

4. `recovered_logs` の直下は元のdata直下に対応する。
   既存の監査プログラムは元の絶対パスを参照するため、この新しいフォルダだけでは自動的に切り替わらない。
   元のdataへ戻す際はmanifestの各パスが現在存在しないことを確認し、必要なログだけを元の位置へコピーする。
   既存ファイルや結果manifestを一括上書きしない。不明な場合はコピー前に確認する。

## 承認済み削除対象と保持対象（2026-09-27削除完了）

下表の3種類、計1,917ファイル・14,319,787,990 bytesは利用者承認後に削除済み。
対象外7,099ファイルのハッシュ不変を確認。空フォルダは残している。
S3バックアップは `s3://mt5-ea-validator/backups/20260927/log-archive/`。
ZIPとSHA-256ファイルを保存し、再取得照合済み。
削除記録は `data/log_archives/20260927_all_logs/cleanup_execution/result.json`。

| 区分 | 正確な対象 | 条件・影響 |
|---|---|---|
| 原本957件、約7.09 GB | archive/manifest.jsonのfiles[].pathを元のdata基点で解決した各ファイルのみ | 外部バックアップ照合と承認後。削除すると既存のログ再監査には復元が必要 |
| 全件の復元コピー、約7.09 GB | data/log_archives/20260927_all_logs/restored/ | 原本ではない。削除後も圧縮一式から再作成可能 |
| 小規模試験の復元コピー、約134.42 MB | data/log_archive_pilot/20260927_three_tester_logs/restored/ | 原本ではない。小規模試験のarchiveから再作成可能 |

今回の削除直前に、対象が意図したdata配下に解決されること、reparse pointがないこと、
サイズ・ハッシュがmanifestと一致することを確認した。上記以外を一括削除していない。
今後追加の削除を行う場合にも別途承認と同様の検証が必要。
復元用archive、ZIP、チェックサム、検証JSON、計画・手順、その他の結果データは保持する。
