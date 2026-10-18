"""构建期脚本：从 Microsoft Fluent Emoji（MIT 协议）下载精选 3D 表情作为内置贴纸。

用法：python tools/fetch_emoji.py <fluent_tree.json> <输出 assets/stickers 目录>
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SEL = {
    "表情": [
        ("Grinning face", "大笑"), ("Face with tears of joy", "笑哭"), ("Rolling on the floor laughing", "笑翻"),
        ("Smiling face with heart-eyes", "花痴"), ("Star-struck", "星星眼"), ("Smiling face with hearts", "爱心脸"),
        ("Winking face", "眨眼"), ("Face blowing a kiss", "飞吻"), ("Thinking face", "思考"),
        ("Shushing face", "嘘"), ("Face with hand over mouth", "偷笑"), ("Exploding head", "震惊爆炸"),
        ("Partying face", "庆祝"), ("Smiling face with sunglasses", "酷"), ("Flushed face", "脸红"),
        ("Pleading face", "求求了"), ("Loudly crying face", "大哭"), ("Astonished face", "惊讶"),
        ("Face screaming in fear", "尖叫"), ("Money-mouth face", "发财"), ("Zany face", "调皮"),
        ("Smiling face with open hands", "抱抱"), ("Drooling face", "流口水"), ("Face savoring food", "好吃"),
        ("Nerd face", "学霸"), ("Smirking face", "坏笑"), ("Upside-down face", "倒脸"),
        ("Grinning squinting face", "眯眼笑"), ("Hot face", "好热"), ("Cold face", "好冷"),
        ("Face with rolling eyes", "白眼"), ("Sleeping face", "睡觉"), ("Pouting face", "生气"),
        ("Clown face", "小丑"), ("Face holding back tears", "感动"), ("Saluting face", "敬礼"),
        ("Melting face", "融化"), ("Smiling face with halo", "天使"), ("Cowboy hat face", "牛仔"),
        ("Disguised face", "伪装"),
    ],
    "手势": [
        ("Thumbs up", "点赞"), ("Clapping hands", "鼓掌"), ("OK hand", "OK"), ("Victory hand", "耶"),
        ("Backhand index pointing right", "指右"), ("Backhand index pointing left", "指左"),
        ("Backhand index pointing up", "指上"), ("Backhand index pointing down", "指下"),
        ("Index pointing at the viewer", "指你"), ("Raising hands", "举手欢呼"), ("Folded hands", "拜托"),
        ("Waving hand", "挥手"), ("Flexed biceps", "加油"), ("Heart hands", "比心手"), ("Call me hand", "打电话"),
        ("Love-you gesture", "爱你"), ("Crossed fingers", "好运"), ("Handshake", "握手"), ("Open hands", "张开双手"),
        ("Pinched fingers", "捏手指"), ("Hand with index finger and thumb crossed", "比心"), ("Writing hand", "写字"),
    ],
    "爱心符号": [
        ("Red heart", "红心"), ("Sparkling heart", "闪亮心"), ("Growing heart", "心动"), ("Heart with arrow", "一箭穿心"),
        ("Heart on fire", "燃烧的心"), ("Two hearts", "两颗心"), ("Revolving hearts", "环绕爱心"),
        ("Heart with ribbon", "爱心礼物"), ("Hundred points", "100分"), ("Collision", "爆炸"), ("Sparkles", "闪光"),
        ("Fire", "火"), ("High voltage", "闪电"), ("Star", "星星"), ("Glowing star", "发光星"), ("Dizzy", "眩晕星"),
        ("Check mark button", "对勾"), ("Cross mark", "叉"), ("Red exclamation mark", "感叹号"),
        ("Red question mark", "问号"), ("Exclamation question mark", "惊问"), ("Double exclamation mark", "双感叹"),
        ("Warning", "警告"), ("Prohibited", "禁止"), ("New button", "NEW"), ("Free button", "FREE"),
        ("Up! button", "UP"), ("Cool button", "COOL"), ("Sos button", "SOS"), ("Top arrow", "TOP"),
        ("Right arrow", "右箭头"), ("Left arrow", "左箭头"), ("Up arrow", "上箭头"), ("Down arrow", "下箭头"),
        ("Bell", "铃铛"), ("Speech balloon", "对话框"), ("Thought balloon", "思考泡"), ("Anger symbol", "怒"),
        ("Musical notes", "音符"), ("Light bulb", "灵感"), ("Magnifying glass tilted left", "放大镜"),
        ("Trophy", "奖杯"), ("Crown", "皇冠"), ("Gem stone", "钻石"), ("Party popper", "礼花"),
        ("Confetti ball", "彩球"), ("Balloon", "气球"), ("Wrapped gift", "礼物"), ("Ribbon", "蝴蝶结"),
        ("Rocket", "火箭"), ("Alarm clock", "闹钟"), ("Hourglass done", "沙漏"), ("Stopwatch", "秒表"),
        ("Pushpin", "图钉"), ("Megaphone", "喇叭"), ("Loudspeaker", "扩音器"), ("Bullseye", "命中"),
        ("Chart increasing", "上涨"), ("1st place medal", "金牌"), ("Rainbow", "彩虹"), ("Sun", "太阳"),
        ("Snowflake", "雪花"), ("Droplet", "水滴"), ("Crescent moon", "月亮"), ("Eyes", "眼睛"),
    ],
    "购物美食": [
        ("Shopping cart", "购物车"), ("Shopping bags", "购物袋"), ("Money bag", "钱袋"), ("Dollar banknote", "美元"),
        ("Yen banknote", "钞票"), ("Coin", "金币"), ("Credit card", "银行卡"), ("Package", "快递盒"),
        ("Label", "标签"), ("Receipt", "小票"), ("Delivery truck", "货车"), ("Mobile phone", "手机"),
        ("Laptop", "笔记本电脑"), ("Camera", "相机"), ("Television", "电视"), ("Watch", "手表"),
        ("Lipstick", "口红"), ("Ring", "戒指"), ("Handbag", "手提包"), ("Purse", "钱包"),
        ("High-heeled shoe", "高跟鞋"), ("Running shoe", "运动鞋"), ("T-shirt", "T恤"), ("Dress", "连衣裙"),
        ("Sunglasses", "墨镜"), ("Teddy bear", "泰迪熊"), ("Bouquet", "花束"), ("Rose", "玫瑰"),
        ("Cherry blossom", "樱花"), ("Sunflower", "向日葵"), ("Four leaf clover", "四叶草"),
        ("Birthday cake", "生日蛋糕"), ("Shortcake", "蛋糕"), ("Doughnut", "甜甜圈"), ("Cookie", "饼干"),
        ("Chocolate bar", "巧克力"), ("Candy", "糖果"), ("Lollipop", "棒棒糖"), ("Hot beverage", "热饮"),
        ("Bubble tea", "奶茶"), ("Tropical drink", "饮料"), ("Clinking glasses", "干杯"), ("Pizza", "披萨"),
        ("Hamburger", "汉堡"), ("French fries", "薯条"), ("Steaming bowl", "面条"), ("Sushi", "寿司"),
        ("Watermelon", "西瓜"), ("Strawberry", "草莓"), ("Cherries", "樱桃"), ("Peach", "桃子"),
        ("Grapes", "葡萄"), ("Lemon", "柠檬"), ("Avocado", "牛油果"), ("Red apple", "苹果"), ("Ice cream", "冰淇淋"),
    ],
    "萌宠动物": [
        ("Cat face", "猫"), ("Dog face", "狗"), ("Panda", "熊猫"), ("Rabbit face", "兔子"), ("Bear", "熊"),
        ("Fox", "狐狸"), ("Unicorn", "独角兽"), ("Chicken", "小鸡"), ("Penguin", "企鹅"), ("Butterfly", "蝴蝶"),
        ("Honeybee", "蜜蜂"), ("Hatching chick", "破壳小鸡"), ("Hamster", "仓鼠"), ("Koala", "考拉"),
        ("Tiger face", "老虎"), ("Monkey face", "猴子"), ("See-no-evil monkey", "捂眼猴"), ("Pig face", "猪"),
        ("Frog", "青蛙"), ("Dolphin", "海豚"),
    ],
}


def main():
    tree_path, out_root = sys.argv[1], sys.argv[2]
    tree = json.load(open(tree_path, encoding="utf-8"))["tree"]
    by_folder = {}
    for t in tree:
        p = t["path"]
        if not p.endswith(".png") or "/3D/" not in p:
            continue
        folder = p.split("/")[1]
        lower = folder.lower()
        # 优先非肤色版本 / Default 肤色
        prio = 0 if p.count("/") == 3 else (1 if "/Default/" in p else 9)
        if lower not in by_folder or prio < by_folder[lower][0]:
            by_folder[lower] = (prio, p)
    missing = []
    count = 0
    for cat, items in SEL.items():
        d = os.path.join(out_root, "表情_" + cat)
        os.makedirs(d, exist_ok=True)
        for en, zh in items:
            hit = by_folder.get(en.lower())
            if not hit:
                missing.append(en)
                continue
            out = os.path.join(d, zh + ".png")
            if os.path.isfile(out) and os.path.getsize(out) > 1000:
                count += 1
                continue
            url = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/" + urllib.parse.quote(hit[1])
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url, timeout=60) as r:
                        data = r.read()
                    with open(out, "wb") as fh:
                        fh.write(data)
                    count += 1
                    break
                except Exception as e:  # noqa
                    time.sleep(1.5)
            else:
                missing.append(en + " (下载失败)")
    print("downloaded", count)
    print("missing", missing)


if __name__ == "__main__":
    main()
