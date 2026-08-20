# vocab.py の再生成

チャンピオン追加やパッチ更新に追従したいとき、Riot の Data Dragon から
最新データを取り直して `vocab.py` を作り直す。

```bash
VER=$(curl -s https://ddragon.leagueoflegends.com/api/versions.json | python3 -c "import sys,json;print(json.load(sys.stdin)[0])")
curl -s "https://ddragon.leagueoflegends.com/cdn/$VER/data/ja_JP/champion.json" -o champ.json
curl -s "https://ddragon.leagueoflegends.com/cdn/$VER/data/ja_JP/item.json" -o item.json
```

チャット履歴（Discord Chat Exporter の txt）を使って頻度順を作り直す場合は、
本文だけを抜き出した `corpus.txt` を用意し、`PROMPT_TERMS` の並びを
出現回数の降順にする。呼び名は `MEMBERS` を直接編集。

`ALL_TERMS` は補正辞書。多くても副作用は無いので全チャンピオン・全アイテムを入れてよい。
`PROMPT_TERMS` は先頭から文字数で打ち切られるため、**実際に口に出す語だけ**を
上位に置くこと。
