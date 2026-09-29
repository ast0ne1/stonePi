# X3 section-page icons

150×150 pure black-and-white PNGs shown on each category's section page in the
reader paper. `render_category_icon` in `app/services/cover_image.py` loads them by
category key; Nordic and Australia (no country icons in Material) and any custom
category without a file are drawn in code instead.

| File | Material Symbols icon |
|------|-----------------------|
| `news.png` | `newspaper` |
| `technology.png` | `memory` |
| `security.png` | `shield_lock` |
| `science.png` | `science` |
| `business.png` | `business_center` |
| `sport.png` | `trophy` |
| `culture.png` | `theater_comedy` |
| `longreads.png` | `menu_book` |

Source: [Material Symbols Outlined](https://fonts.google.com/icons) by Google, style
Fill 1, weight 600, grade 0, optical size 48. Each glyph was centred on its ink box
at 124px inside the 150px canvas, then thresholded to pure #000 / #fff. Licensed
under the Apache License 2.0; see `LICENSE-material-symbols.txt`.

To swap an icon, export the new glyph the same way and replace the PNG with the
same file name; `tests/test_x3_layout.py` checks size, colours, and that no two
categories share an icon.
