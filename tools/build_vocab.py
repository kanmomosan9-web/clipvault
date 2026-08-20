import json, re, collections
from pathlib import Path

corpus = Path("corpus.txt").read_text(encoding="utf-8")
champ = json.load(open("champ.json"))["data"]
items = json.load(open("item.json"))["data"]

# --- チャンピオン（日本語名 + チャットで使われる短縮形） ---
champions = sorted({v["name"] for v in champ.values()})

# --- アイテム：完成品と主要パーツのみ（ノイズ除去） ---
def keep_item(v):
    g = v.get("gold", {})
    if not g.get("purchasable"):
        return False
    if v.get("maps", {}).get("11") is False:
        return False
    if g.get("total", 0) < 1100:
        return False
    if "consumed" in v and v["consumed"]:
        return False
    return True

item_names = sorted({v["name"] for v in items.values() if keep_item(v)})

OBJECTIVES = [
    "ドラゴン","ドラゴンソウル","エルダードラゴン","インファーナルドレイク","マウンテンドレイク",
    "オーシャンドレイク","クラウドドレイク","ヘクステックドレイク","ケミテックドレイク",
    "バロン","バロンナシャール","ヘラルド","リフトヘラルド","ヴォイドグラブ","グラブ",
    "アタカーン","スカトルクラブ","スカトル","ハーピー","レイス","ゴーレム","レッドバフ",
    "ブルーバフ","クルーグ","ラプター","ウルフ","グロンプ","キャノン","タワー","タレット",
    "インヒビター","ネクサス","ミニオン","キャノンミニオン","スーパーミニオン","プレート",
]

JARGON = [
    "ガンク","カウンターガンク","ローム","ファーム","ラストヒット","シージ","ダイブ","フリーズ",
    "プッシュ","スロープッシュ","ウェーブ","リコール","バックドア","スプリットプッシュ","スワップ",
    "ロール","レーン","トップ","ミッド","ボット","ジャングル","サポート","ADC","APC","メイジ",
    "タンク","ブルーザー","アサシン","エンチャンター","フラッシュ","イグナイト","テレポート","TP",
    "ゴースト","エクスハースト","バリア","ヒール","クレンズ","スマイト","サモナースペル","ウルト",
    "スキル","パッシブ","クールダウン","CD","マナ","AD","AP","CC","スタン","ノックアップ","スロウ",
    "サイレンス","チャーム","フィア","タウント","サプレッション","シールド","ヒール","ライフスティール",
    "ドレイン","アーマー","MR","貫通","ペネトレーション","クリティカル","攻撃速度","AS",
    "ワード","コントロールワード","ピンクワード","スイーパー","トリンケット","ビジョン","デワード",
    "オールイン","トレード","ポーク","キャッチ","エンゲージ","ディスエンゲージ","ピール","フォーカス",
    "カイティング","オーブウォーク","アニメーションキャンセル","スキルショット","フック",
    "集団戦","ファイト","ピック","バン","ドラフト","カウンターピック","コンプ","構成","スケール",
    "パワースパイク","スノーボール","キャリー","スマーフ","トロール","フィード","フィーダー",
    "AFK","GG","GLHF","ミア","ミス","エース","ペンタキル","クアドラキル","トリプルキル","ダブルキル",
    "ファーストブラッド","シャットダウン","バウンティ","ソロキル","カウンター","マッチアップ",
    "CS","KDA","ゴールド","レベル","ルーン","マスタリー","ビルド","コアアイテム","ミシック",
    "レジェンダリー","ブーツ","スペルシールド","バーストダメージ","DPS","タンキー","スクイシー",
    "サレンダー","サレ","降参","リメイク","ランク","ソロキュー","フレックス","デュオ","フルパ",
    "アイアン","ブロンズ","シルバー","ゴールド","プラチナ","エメラルド","ダイヤ","マスター",
    "グランドマスター","チャレンジャー","LP","MMR","エロヘル","オートフィル","ダッジ",
    "ロル","LoL","ヴァロラント","ヴァロ","TFT","ARAM","ハウリングアビス","サモナーズリフト",
]

MEMBERS = ["つるかん","鶴田","たいち","やしき","屋敷","じんぼ","神保","しげん","なみき","並木"]

# --- チャット出現頻度でランキング ---
def freq(term):
    return corpus.count(term)

pool = []
for t in champions:
    pool.append((t, freq(t), "champion"))
for t in item_names:
    pool.append((t, freq(t), "item"))
for t in OBJECTIVES:
    pool.append((t, freq(t), "objective"))
for t in JARGON:
    pool.append((t, freq(t), "jargon"))

used = [(t, n, c) for t, n, c in pool if n >= 5]
used.sort(key=lambda x: -x[1])

print(f"チャンピオン {len(champions)}体 / アイテム {len(item_names)}件 / "
      f"オブジェクト {len(OBJECTIVES)} / 用語 {len(JARGON)}")
print(f"うちチャットに5回以上登場: {len(used)}語\n")
print("=== 実際に使われている語 上位60 ===")
for t, n, c in used[:60]:
    print(f"{t}({n})", end="  ")
print("\n")

Path("ranked.json").write_text(json.dumps(
    {"ranked": [[t, n, c] for t, n, c in used],
     "champions": champions, "items": item_names,
     "objectives": OBJECTIVES, "jargon": JARGON, "members": MEMBERS},
    ensure_ascii=False), encoding="utf-8")
