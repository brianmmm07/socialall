# Design

Built with the [Impeccable](https://impeccable.style) design guidelines.

**Mode:** Operate. People come here to scan a lot of posts quickly, so the UI
stays familiar and gets out of the way. Media is the content; chrome is quiet.

**World:** a dark viewing room. Photos and video read best on a near-black
background, and that is the main use: browsing media, often for a while.

- **Colour:** one warm neutral ramp (`--bg` to `--surface-3`) and one amber
  accent, used only for primary actions, the current item, focus and starred
  state. Each platform has a colour, shown only as a small dot.
- **Type:** the system sans (with CJK fallbacks for Xiaohongshu), one family,
  tight scale. Numbers use tabular figures.
- **Feed:** a waterfall, like the apps it collects from. Media sits in a fixed
  4:5 frame so the layout never shifts while images load. Text-only posts get
  larger type instead of a fake image.
- **Viewer:** full-screen dialog. Media on the left, post details on the right
  (below on phones). Post navigation sits with Close in the top-right; image
  arrows sit on the image.
- **Icons:** one inline SVG set, 1.8 stroke, no emoji.
- **Motion:** 140–250 ms ease-out for state changes only. Reduced motion is
  respected.
