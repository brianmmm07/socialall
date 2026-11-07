# Design

Built with the [Impeccable](https://impeccable.style) design guidelines.

**Mode:** Operate. People come here to scan a lot of posts quickly, so the UI
stays familiar and gets out of the way. Media is the content; chrome is quiet.

**World:** warm paper. A cream page with off-white tiles that lift on soft,
warm shadows, espresso-brown text, and a terracotta accent. It should feel like
a scrapbook of things worth keeping rather than a control room.

- **Colour:** one warm neutral ramp (`--bg` cream, `--surface` off-white,
  `--surface-2/3` deeper cream) and one terracotta accent, used only for primary
  actions, the current item, focus and starred state. Every text colour passes
  4.5:1 on every surface. Each platform has a colour, shown only as a small dot.
- **Depth:** tiles sit on soft warm shadows (offset plus blur, never a colour
  halo) and rise slightly on hover.
- **Type:** the system sans (with CJK fallbacks for Xiaohongshu), one family,
  tight scale. Numbers use tabular figures.
- **Feed:** a waterfall, like the apps it collects from. Media sits in a fixed
  4:5 frame so the layout never shifts while images load. Text-only posts get
  larger type instead of a fake image.
- **Viewer:** full-screen dialog on the cream page. Media on a deeper cream
  stage on the left (video itself plays on a dark frame), post details on the
  right (below on phones). Post navigation sits with Close in the top-right; image
  arrows sit on the image.
- **Icons:** one inline SVG set, 1.8 stroke, no emoji.
- **Motion:** 140–250 ms ease-out for state changes only. Reduced motion is
  respected.
