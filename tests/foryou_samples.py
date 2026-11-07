"""What the For you pages receive from each site, cut down to the parts SocialAll reads."""


def ig_media(pk, code, user="cafe", **extra):
    return {
        "pk": pk, "id": f"{pk}_111", "code": code, "media_type": 1, "taken_at": 1730800000,
        "user": {"pk": "111", "username": user}, "caption": {"text": f"{code} latte art"},
        "image_versions2": {"candidates": [
            {"url": f"https://scontent.cdninstagram.com/{code}-s.jpg", "width": 320},
            {"url": f"https://scontent.cdninstagram.com/{code}-l.jpg", "width": 1080},
        ]},
        "like_count": 120, "comment_count": 4, **extra,
    }


IG_CAROUSEL = ig_media("3402", "Ab2", media_type=8, carousel_media=[
    # A carousel's parts look a lot like posts; they mustn't turn into posts of their own.
    ig_media("3412", "Ab2", media_type=1),
    {"pk": "3413", "media_type": 2, "image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/c2.jpg", "width": 1080}]},
     "video_versions": [{"url": "https://scontent.cdninstagram.com/c2.mp4", "width": 720}]},
])
IG_REEL = ig_media("3403", "Ab3", "roaster", media_type=2, product_type="clips", play_count=9000,
                   video_versions=[{"url": "https://scontent.cdninstagram.com/Ab3.mp4", "width": 720}])
IG_AD = ig_media("3404", "Ab4", "brand", ad_id="9999", ad_action="Shop now")
IG_STORY = ig_media("3405", "Ab5", product_type="story", expiring_at=1730900000)

# The first posts come inside the page, the rest from the page's own requests as you scroll.
IG_PAGE = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"result": {"data": {
    "xdt_api__v1__feed__timeline__connection": {"edges": [{"node": {"media": ig_media("3401", "Ab1")}}]}}}}}]]]}
IG_TIMELINE = {"data": {"xdt_api__v1__feed__timeline__connection": {
    "edges": [
        {"node": {"media": IG_CAROUSEL}},
        {"node": {"media": IG_REEL}},
        {"node": {"media": IG_AD}},
        {"node": {"media": ig_media("3401", "Ab1")}},  # seen already
    ],
    "page_info": {"has_next_page": True, "end_cursor": "x"},
}, "xdt_api__v1__feed__reels_tray": {"tray": [{"items": [IG_STORY]}]}}}

TT_ITEMS = {"statusCode": 0, "itemList": [
    {"id": "7430000000000000001", "desc": "latte art #coffee", "createTime": 1730800000,
     "author": {"uniqueId": "bob", "nickname": "Bob"},
     "video": {"cover": "https://p16-sign.tiktokcdn-us.com/c.jpeg", "playAddr": "https://v16-webapp.tiktok.com/v.mp4"},
     "stats": {"diggCount": 1500, "commentCount": 20, "shareCount": 3, "playCount": 45000}},
    {"id": "7430000000000000002", "desc": "photo dump", "createTime": "1730700000", "author": {"uniqueId": "amy"},
     "imagePost": {"images": [{"imageURL": {"urlList": ["https://p16.tiktokcdn.com/1.jpeg"]}},
                              {"imageURL": {"urlList": ["https://p16.tiktokcdn.com/2.jpeg"]}}]},
     "video": {}, "statsV2": {"diggCount": "2300", "playCount": "120000"}},
    {"id": "7430000000000000003", "desc": "buy this", "createTime": 1730600000, "isAd": True,
     "author": {"uniqueId": "shop"}, "video": {"cover": "https://p16.tiktokcdn.com/ad.jpeg"}},
]}

XHS_FEED = {"code": 0, "success": True, "data": {"cursor_score": "1.7", "items": [
    {"id": "6720aaaaaaaaaaaaaaaaaaaa", "model_type": "note", "xsec_token": "ABxyz=",
     "note_card": {"type": "normal", "display_title": "周末咖啡", "user": {"nickname": "小李", "user_id": "5f01"},
                   "cover": {"url_default": "http://sns-webpic-qc.xhscdn.com/202411/abc!nc_n_webp_mw_1", "width": 1080},
                   "interact_info": {"liked_count": "1.2万", "liked": False}}},
    {"id": "6720bbbbbbbbbbbbbbbbbbbb", "model_type": "note", "xsec_token": "CD+/=",
     "note_card": {"type": "video", "display_title": "拉花教程", "user": {"nick_name": "阿明"},
                   "cover": {"info_list": [{"image_scene": "WB_PRV", "url": "http://sns-webpic-qc.xhscdn.com/p.jpg"},
                                           {"image_scene": "WB_DFT", "url": "http://sns-webpic-qc.xhscdn.com/d.jpg"}]},
                   "interact_info": {"liked_count": "856"}}},
    {"id": "6720cccccccccccccccccccc", "model_type": "ads", "note_card": {"display_title": "广告", "cover": {"url": "https://x.xhscdn.com/a.jpg"}}},
]}}
XHS_ME_GUEST = {"code": 0, "success": True, "data": {"guest": True, "user_id": "6600"}}
XHS_ME = {"code": 0, "success": True, "data": {"user_id": "5f00", "nickname": "Brian", "red_id": "123"}}
