"""Reviews shown on the ratings screen.

main.py pulls the whole backlog from here and merges it with whatever has
been added through the bot's own admin "Add Rating" tool (those live in the
`reviews` table, not here). A review from the database and a review from
this file are interchangeable - both are read as
(date_str, stars, amount, products, text), the same five columns
db_add_review() writes, in the same order - so filter_reviews() and
format_review() work on either without needing to know which one it is.

ALL_REVIEWS below is this shop's real review history. The first block (newest)
carries its own real amount and product(s), pulled from the live ratings
page. The legacy block after it is carried over from an earlier import with
`amount` and `products` left blank: there is no real order amount or product
on record for any single one of those, and making figures up to sit next to
a genuine customer's words would be inventing a transaction detail that
never existed. Reviews added from now on through the admin panel carry their
own real amount and product, typed in at the time.

BASE_MONTHLY / BASE_WEEKLY are placeholders, not a measured figure - see the
note by them below. BASE_TOTAL is not a placeholder in the same sense: it
also sets how many pages the *unfiltered* "all reviews" screen believes it
has, a quirk of how main.py's reviews_content() works out page count for
that one view (it uses this number instead of counting the reviews it
actually has). Set it below len(ALL_REVIEWS) and the UI's own Next button
stops reaching most of the backlog, even though every review is still
sitting there in the data - nothing crashes, the pager just runs out early.
Nothing else here needs touching by hand; STAR_DIST is worked out from
ALL_REVIEWS automatically.
"""
import html

# ---------------------------------------------------------------------------
# "Total / Monthly / Weekly" on the ratings header - the big running number a
# shop shows off, not computed from anything, just entered here. BASE_TOTAL
# starts equal to how many reviews are actually on file, which is true today
# and keeps every one of them reachable through the pager; raise it to a real
# total-sales figure once there is one - as long as it stays at or above
# len(ALL_REVIEWS) the pager keeps working. BASE_MONTHLY/BASE_WEEKLY are
# purely cosmetic "+N" figures and 0 is an honest "not set" until there are
# real ones to put here.
# ---------------------------------------------------------------------------
BASE_MONTHLY = 0
BASE_WEEKLY = 0

REVIEWS_PER_PAGE = 5

# Not read anywhere in main.py - imported for the name only. Kept as the
# star values in display order, in case something later wants to iterate
# over them.
STARS = (5, 4, 3, 2, 1)

ALL_REVIEWS = [
    # Real reviews pulled from the live ratings page, with their actual
    # amount and product(s) - unlike the legacy backlog below, which has
    # neither on record. Kept newest first, matching the order shown there.
    ('3 Nov, 2026 at 11:17', 2, '£57.81', 'Afghani Black Glass  🇦🇫', "TWO STARS - What the hell happened to this glass? It's nowhere near the quality it was. It runs red, it runs super quick and burns out. It's about as good as street gear. Mono has great service but his products have seriously gone downhill. The glass used to be his top product, but this is nowhere near the quality it was. Go elsewhere."),
    ('3 Nov, 2026 at 11:06', 5, '£29.99', 'Strawberry Cheesecake🍓🧀', 'Loooovely'),
    ('3 Nov, 2026 at 11:05', 5, '£208.99', '🍫 Cali Plates Static Sift – Chocolato 🇺🇸 70-130u, Strawberry Cheesecake🍓🧀, Pakistani "PK" Cream 🇵🇰', '10/10 was a bit gutted about the pk running out but the black glass is just about the same imo. Either way im happy so thanks mono and the team 💜'),
    ('3 Nov, 2026 at 09:25', 5, '£110.00', 'Pakistani #4 (China White)', 'Banging, especially with 20% off. Recommend hot plate to make it go further and make it easier to use'),
    ('3 Nov, 2026 at 02:43', 5, '£133.25', 'PURE Columbian Cocaine 🇨🇴, Afghani Black Glass  🇦🇫', 'Fantastic product awesome discounted price'),
    ('1 Nov, 2026 at 21:21', 5, '£138.88', 'Pakistani "PK" Cream 🇵🇰', 'Proper stuff. A+'),
    ('1 Nov, 2026 at 18:52', 5, '£257.88', 'Pakistani "PK" Cream 🇵🇰', 'Great stuff, great prices, very professional.'),
    ('1 Nov, 2026 at 01:48', 5, '£124.00', 'Afghani Black Glass  🇦🇫', 'Absolutely banging staff, ive tried all off them and black glass is the best'),
    ('31 Oct, 2026 at 22:50', 5, '£171.60', 'PURE Columbian Cocaine 🇨🇴', 'Perfect. Love this stuff'),
    ('31 Oct, 2026 at 19:23', 3, '£124.00', 'Afghani Black Glass  🇦🇫', 'Not bad, not as good as my last order was mostly grit n just wasnt as satisfying as usual'),
    ('31 Oct, 2026 at 16:56', 1, '£25.00', 'PURE Columbian Cocaine 🇨🇴', 'Really poor. Never have had anything but good to say about mono. Always top notch but this stuff is cut with magic. Horrible to smoke.'),
    ('31 Oct, 2026 at 14:39', 5, '£180.75', 'Afghani Black Glass  🇦🇫, Bicarbonate Crack Cocaine 🇨🇴, PURE Columbian Cocaine 🇨🇴', 'NDD, all 🔥'),
    ('31 Oct, 2026 at 13:19', 5, '£275.94', 'Pakistani "PK" Cream 🇵🇰', 'NDD (as always), quality stuff and thanks for the little extra!'),
    ('31 Oct, 2026 at 13:09', 5, '£77.25', 'Afghani Black Glass  🇦🇫', 'Superb as always'),
    ('31 Oct, 2026 at 13:09', 5, '£52.81', 'Afghani Black Glass  🇦🇫', "Just exceptional quality and service! Deals, on deals, no reason why you'd go elsewhere"),
    ('31 Oct, 2026 at 11:15', 5, '£52.46', 'Strawberry Cheesecake🍓🧀', 'First order with the seller. Such a smooth smoke and quite a heavy hitter. Will definitely be back. NDD.'),
    ('30 Oct, 2026 at 21:37', 5, '£105.00', '"A-Y" Stamp Cocaine 🇨🇴', 'NDD. All good as usual'),
    ('30 Oct, 2026 at 21:07', 5, '£129.00', 'Afghani Black Glass  🇦🇫, Bicarbonate Crack Cocaine 🇨🇴', 'Again. Noththing new to say…… top vendor, top admin and top product. By far best of the rest'),
    ('30 Oct, 2026 at 19:53', 5, '£171.60', 'PURE Columbian Cocaine 🇨🇴', "Very oily but when dried up it's very good"),
    ('30 Oct, 2026 at 19:46', 5, '£196.25', 'Pakistani "PK" Cream 🇵🇰', 'NDD, good bit of pk!'),
    ('30 Oct, 2026 at 19:05', 5, '£60.25', 'Pakistani #4 (China White)', '2DD (Should of been NDD but RM had issues in my area)) Lovely stuff, beautiful nod. The best on tess'),
    ('30 Oct, 2026 at 18:00', 5, '£228.13', 'Afghani Black Glass  🇦🇫', '💯💯💯👍👍👍👍'),
    ('30 Oct, 2026 at 17:53', 5, '£160.33', 'Strawberry Cheesecake🍓🧀, ROCK KETAMINE', 'Oooooo do not listen to the negative...all good 😀'),
    ('30 Oct, 2026 at 17:05', 5, '£90.00', 'PURE Columbian Cocaine 🇨🇴', 'Goooood ahit bro'),
    ('30 Oct, 2026 at 14:40', 5, '£139.99', 'Strawberry Cheesecake🍓🧀, Pakistani Meth 🇵🇰, "A-Y" Stamp Cocaine 🇨🇴', 'Start'),
    ('30 Oct, 2026 at 14:14', 5, '£124.00', 'Afghani Black Glass  🇦🇫', 'NDD again thanks. Lovely smoke'),
    ('30 Oct, 2026 at 14:07', 5, '£201.00', '"A-Y" Stamp Cocaine 🇨🇴', 'I have got to say this was a bit of a leap of faith for me, very happy with my previous vendor, but this is exactly the same just slightly cheaper. 10/10 service and 9/10 product. How do i join the private club?'),
    ('30 Oct, 2026 at 13:56', 1, '£52.50', '🌨️ MONOPOLY SPEEDBALL 💉', "The heroin supplied was complete garbage. A decent vendor wouldnt have that lower quality product in stock. This was my last order with mono and not ordered since to avoid shit like this.\nSeller's reply: These orders are from July, 4 months ago"),
    ('30 Oct, 2026 at 13:54', 1, '£149.47', 'Cookie Dawg 🍪🐶, 🍫 Cali Plates Static Sift – Chocolato 🇺🇸 70-130u', "'Static' is fake albo poison garbage. Gives off endless fumes\nSeller's reply: These orders are from July, 4 months ago"),
    ('30 Oct, 2026 at 13:53', 3, '£91.75', '🍌🍇Wonka Melts Piatella Hash Banana Jelly🇺🇸, "A-Y" Stamp Cocaine 🇨🇴, Afghani Dream 🇦🇫', "'Hash' is fake albo poison garbage\nSeller's reply: These orders are from July, 4 months ago"),
    ('30 Oct, 2026 at 13:28', 5, '£132.33', 'Strawberry Cheesecake🍓🧀', 'NDD. Wonderful stuff, as always. Great service from a top tier vendor. A+++'),
    ('30 Oct, 2026 at 12:46', 5, '£130.80', 'Afghani Black Glass  🇦🇫', 'Arrived despite no tracking numbers, mono is a machine'),
    ('30 Oct, 2026 at 10:11', 5, '£94.25', 'Pakistani Meth 🇵🇰', 'Great quality gear. Smokes really well and really nice high. Delivered next day and well packaged.'),
    ('30 Oct, 2026 at 09:00', 5, '£45.00', '"A-Y" Stamp Cocaine 🇨🇴', 'Perfect. Good enough stealth, NDD, good coms and helped me laugh off Mr Cameroon. 😆 not bad bit of white for price. Cooks up well and goes well in a speedball. Cheers guys'),
    ('30 Oct, 2026 at 07:21', 5, '£58.10', 'Afghani Black Glass  🇦🇫, ROCK KETAMINE', 'Perfect job yet again. Perfect product. NDD. God comms. Impressed 🫡 Thanks team.'),
    ('30 Oct, 2026 at 05:28', 5, '£52.50', 'Bicarbonate Crack Cocaine 🇨🇴', 'Fast and discreet delivery and spot on and product'),
    ('29 Oct, 2026 at 23:22', 5, '£81.50', 'Afghani Black Glass  🇦🇫, PURE Columbian Cocaine 🇨🇴', 'Outstanding rm fked me took week to come not ur fault bro be bk for big xmas order'),
    ('29 Oct, 2026 at 21:57', 5, '£82.25', 'Afghani Black Glass  🇦🇫', 'Good'),
    ('29 Oct, 2026 at 18:36', 5, '£141.00', 'Afghani Black Glass  🇦🇫', 'NDD thanks really nice smoke'),
    ('29 Oct, 2026 at 17:41', 5, '£126.42', '"A-Y" Stamp Cocaine 🇨🇴', 'Ndd, ideal thanks team 👍'),
    ('29 Oct, 2026 at 16:18', 5, '£132.33', 'Strawberry Cheesecake🍓🧀', 'Ndd nice solid nuggets and a very nice smoke'),
    ('29 Oct, 2026 at 16:14', 5, '£16.90', 'ROCK KETAMINE', 'AWESOME'),
    ('29 Oct, 2026 at 16:14', 5, '£26.25', 'PURE Columbian Cocaine 🇨🇴', 'AWESOME'),
    ('29 Oct, 2026 at 15:22', 5, '£286.63', 'Bicarbonate Crack Cocaine 🇨🇴, Pakistani "PK" Cream 🇵🇰', 'NDD, the cream was a delight. Very potent. The white was very strong'),
    ('29 Oct, 2026 at 15:21', 5, '£158.00', 'Afghani Black Glass  🇦🇫', 'NDD excellent gear, absolutely love this stuff'),
    ('29 Oct, 2026 at 14:25', 5, '£199.32', 'PURE Columbian Cocaine 🇨🇴, Afghani Black Glass  🇦🇫', 'Both priced at awesome offers and impressive high'),
    ('29 Oct, 2026 at 14:24', 5, '£140.50', 'PURE Columbian Cocaine 🇨🇴, Afghani Black Glass  🇦🇫', 'I love the glass so much daily order every day'),
    ('29 Oct, 2026 at 14:23', 5, '£65.75', 'Afghani Black Glass  🇦🇫', 'Love it'),
    ('29 Oct, 2026 at 14:22', 5, '£85.00', 'PURE Columbian Cocaine 🇨🇴', 'Well worth the price and then some'),
    ('29 Oct, 2026 at 14:21', 5, '£100.00', 'Pakistani "PK" Cream 🇵🇰', 'Really nice and clean'),
    ('29 Oct, 2026 at 14:20', 5, '£133.25', 'PURE Columbian Cocaine 🇨🇴, Afghani Black Glass  🇦🇫', "Wasn't sure if Charlie for such a wicked price would've been stamped on but really impressed"),
    ('29 Oct, 2026 at 14:04', 1, '£37.00', 'PURE Columbian Cocaine 🇨🇴', "Absolutely rubbish got cut in it that they used call magic won't wash back and even sniff is waste gutted first bad experience 😢"),
    ('29 Oct, 2026 at 13:30', 5, '£70.00', '"A-Y" Stamp Cocaine 🇨🇴', 'First time ordering of SirMonopoly and really pleased with the quality! This stuff is smooth asf! Definitely will be returning customer. Cheers guys!'),
    ('29 Oct, 2026 at 12:36', 5, '£110.00', '"A-Y" Stamp Cocaine 🇨🇴', 'Banging stuff 💯👌'),
    ('29 Oct, 2026 at 12:19', 5, '£330.13', 'Strawberry Cheesecake🍓🧀', "Lovely stuff as always thanks again I'll be back"),
    ('29 Oct, 2026 at 11:54', 5, '£137.33', 'Strawberry Cheesecake🍓🧀', 'Love the bud.....'),
    ('29 Oct, 2026 at 11:50', 5, '£90.00', 'PURE Columbian Cocaine 🇨🇴, Pakistani Meth 🇵🇰', 'Need to hotplate the colo, descent for the price, came next day, not tried the pm, cheers team!'),
    ('29 Oct, 2026 at 11:45', 5, '£57.00', 'PURE Columbian Cocaine 🇨🇴', 'Spot on as always , rapid delivery'),
    ('29 Oct, 2026 at 10:55', 5, '£132.33', 'Strawberry Cheesecake🍓🧀', 'Ndd as allways mono never fails for me and the quality of thos cheese cake is off the charts super light green buds not to try taste and smoke lovely King mono baby'),
    ('29 Oct, 2026 at 09:54', 5, '£783.50', 'Afghani Black Glass  🇦🇫', 'Thanks mono ndd this gear is top of the opium tree 11/10.'),
    ('29 Oct, 2026 at 08:15', 4, '£100.00', 'PURE Columbian Cocaine 🇨🇴', 'Can\'t really be pissed for 90 deffo wasn\'t "pure"'),
    ('28 Oct, 2026 at 22:11', 5, '£312.40', 'PURE Columbian Cocaine 🇨🇴', 'Start'),
    ('28 Oct, 2026 at 21:55', 5, '£241.25', 'Afghani Black Glass  🇦🇫', '💯💯💯👍👍👍'),
    ('28 Oct, 2026 at 20:18', 5, '£107.60', 'SHARD KETAMINE, PURE Columbian Cocaine 🇨🇴', 'Ndd top shelf A++++'),
    ('28 Oct, 2026 at 19:10', 5, '£257.88', 'Pakistani "PK" Cream 🇵🇰', 'NDD, lovely bit of PK!'),
    ('28 Oct, 2026 at 18:48', 5, '£502.25', 'Strawberry Cheesecake🍓🧀', "All good. I'll be back for more"),
    ('28 Oct, 2026 at 18:48', 5, '£481.00', 'Grape Goo 🍇🫧', '5☆ as always'),
    ('28 Oct, 2026 at 18:19', 5, '£257.88', 'Pakistani "PK" Cream 🇵🇰', 'Exceptional quality -A++ ... NDD ... can not fault the service in any way.. perfection...shout out to monopoly crew. - awesome'),
    ('28 Oct, 2026 at 17:28', 5, '£34.75', '🍫 Cali Plates Static Sift – Chocolato 🇺🇸 70-130u', 'Very good very nice'),
    ('28 Oct, 2026 at 16:20', 5, '£26.25', 'PURE Columbian Cocaine 🇨🇴', 'Creeper has now kicked in next level will be bk'),
    ('28 Oct, 2026 at 16:07', 5, '£426.50', 'Afghani Black Glass  🇦🇫', "nice big lumps few crumbs but can't complain about that to much"),
    ('28 Oct, 2026 at 15:55', 5, '£137.81', 'Pakistani #4 (China White)', 'NDD. Wicked gear and best service on tess. Great team overall. Especially admins who are always available to talk to whether you have a problem with an order, need advice or just someone to chat to on here. Product kicks ass and is good value for ££, especially with the generous 15% off this time. All round excellence. 11/10 everytime.'),
    ('28 Oct, 2026 at 15:30', 5, '£76.19', 'Pakistani #4 (China White), Pakistani "PK" Cream 🇵🇰', 'Ndd, primo product!'),
    ('28 Oct, 2026 at 15:27', 5, '£425.75', 'PURE Columbian Cocaine 🇨🇴, 8 Ball 250mg 🇳🇱', 'Perfect as always. In love with the new colo, absolutely delightful and sooo clean on the nose'),
    ('28 Oct, 2026 at 15:21', 5, '£50.00', 'Pakistani #4 (China White)', 'NDD great quality 👌'),
    ('28 Oct, 2026 at 14:59', 5, '£132.50', 'Afghani Black Glass  🇦🇫', 'NDD. Thanks be back'),
    ('28 Oct, 2026 at 13:50', 5, '£85.75', 'Afghani Black Glass  🇦🇫, Pakistani #4 (China White)', 'NDD, great stealth. CW benefits from hot plate, makes it easier to work with and less chemical taste. ABG also great'),
    ('28 Oct, 2026 at 13:30', 5, '£85.00', 'PURE Columbian Cocaine 🇨🇴', 'NND, perfect. Exactly as described, great value with your special offer. Tvm!'),
    ('28 Oct, 2026 at 13:22', 5, '£132.33', 'Strawberry Cheesecake🍓🧀', 'Ndd, strawberry cheesecake has gotta be one of my favourite flavours, especially grown at this quality. Thankyou monopoly.'),
    ('28 Oct, 2026 at 12:01', 5, '£189.87', 'Strawberry Cheesecake🍓🧀, 🍫 Cali Plates Static Sift – Chocolato 🇺🇸 70-130u, Pakistani "PK" Cream 🇵🇰', '10/10 products 2dd'),
    ('28 Oct, 2026 at 11:05', 5, '£228.13', 'Afghani Black Glass  🇦🇫', 'NDD. 2 nice big lumps and lots of little rocks and some dust. Looks lovely. Well it is lovely in all aspects but the lumps look exceptionally sexy 😂'),
    ('28 Oct, 2026 at 09:04', 5, '£105.00', '"A-Y" Stamp Cocaine 🇨🇴', 'Fantastic very happy 9/10 for the £105. Also look for like 4 not 3.5.'),
    ('28 Oct, 2026 at 09:00', 3, '£85.00', 'PURE Columbian Cocaine 🇨🇴', "Definitely not pure, I got a lump of both the bricks. The first one not great, the second one AY excellent. You can't win them all I suppose."),
    ('28 Oct, 2026 at 07:58', 3, '£76.00', 'ROCK KETAMINE, PURE Columbian Cocaine 🇨🇴', "For the price cant complain but the coke is far from pure or strong (its bashed with oil paste etc.) ket gives you a headache, ive been clean from K 170 days & it didn't do much to much to me with 0 tolerance. You get what you pay for though… If you want good stuff go to somebody who charges double this price"),
    ('28 Oct, 2026 at 07:18', 5, '£45.00', 'Pakistani Meth 🇵🇰', 'Good'),
    ('28 Oct, 2026 at 07:18', 5, '£68.50', 'Pakistani Meth 🇵🇰', 'Good'),
    ('28 Oct, 2026 at 06:59', 5, '£85.00', 'PURE Columbian Cocaine 🇨🇴', 'NDD, weighed up correct. Very oily- hot plate needed. Good for the price. Thanks'),
    ('28 Oct, 2026 at 05:49', 5, '£141.00', 'Afghani Black Glass  🇦🇫', 'All 100% ty to all staff, & quality as always is banging 👌'),
    ('28 Oct, 2026 at 00:07', 5, '£45.50', 'Afghani Black Glass  🇦🇫', 'Superb as always'),
    ('28 Oct, 2026 at 00:07', 5, '£86.50', 'Afghani Black Glass  🇦🇫', 'Brilliant as always'),
    ('28 Oct, 2026 at 00:06', 5, '£131.00', 'Afghani Black Glass  🇦🇫', 'Just absolutely superb quality and service xx'),
    ('27 Oct, 2026 at 23:20', 5, '£47.88', 'Pakistani "PK" Cream 🇵🇰', 'Great stuff NDD'),
    ('27 Oct, 2026 at 23:19', 5, '£27.63', 'Pakistani "PK" Cream 🇵🇰', 'Nice gear NDD'),
    ('27 Oct, 2026 at 23:19', 5, '£116.25', '"A-Y" Stamp Cocaine 🇨🇴', 'Great. NDD'),
    ('27 Oct, 2026 at 23:18', 5, '£311.00', 'Pakistani "PK" Cream 🇵🇰, Afghani Dream 🇦🇫', 'Great gear NDD'),
    ('27 Oct, 2026 at 23:17', 5, '£204.75', 'Bicarbonate Crack Cocaine 🇨🇴, Iranian Crumble Heroin 🇮🇷, Iranian Black Glass  🇮🇷', 'Good product NDD'),
    ('27 Oct, 2026 at 23:17', 5, '£105.00', '"XTZ" Stamp Cocaine 🇨🇴', 'Great yay'),
    ('27 Oct, 2026 at 23:16', 5, '£81.50', 'Pakistani #4 (China White)', 'Nice bit of gear'),
    ('27 Oct, 2026 at 23:16', 5, '£81.50', 'Pakistani #4 (China White)', 'It was good gear'),
    ('27 Oct, 2026 at 23:15', 5, '£95.00', 'Bicarbonate Crack Cocaine 🇨🇴', 'Excellent rock NDD'),
    ('27 Oct, 2026 at 22:29', 5, '£124.00', 'Afghani Black Glass  🇦🇫', 'Ndd fucken beautiful'),
    ('27 Oct, 2026 at 20:02', 5, '£32.00', 'Afghani Black Glass  🇦🇫', 'Thanks team ,respect 👍'),
    ('27 Oct, 2026 at 14:57', 5, '£140.00', 'Pakistani "PK" Cream 🇵🇰', 'Definitely the best gear on this platform. Runs well and v dark hope this stays around for a long time'),
    ('27 Oct, 2026 at 14:56', 3, '£132.33', 'Strawberry Cheesecake🍓🧀', "There's no way this is 28g. More like 14g. Won't be ordering again.."),
    ('27 Oct, 2026 at 14:54', 5, '£57.00', 'PURE Columbian Cocaine 🇨🇴', 'Washes up really well excellent return.'),
    # Legacy backlog - no amount/product on record for these, see the module
    # docstring.
    ('27/10/26', 5, "", "", ''),
    ('25/10/26', 5, "", "", 'Lovely hash 10/10 fast shipping too 👌🏽'),
    ('24/10/26', 5, "", "", ''),
    ('24/10/26', 5, "", "", 'The best in the buisness. Wouldnt go anywhere else always on point and always ndd!'),
    ('24/10/26', 4, "", "", 'Cokes decent, very good for the price. Qualudes unreal. Weed was banging. Bought the isomer k and even though it looked the part, had a very stale tas...'),
    ('23/10/26', 5, "", "", ''),
    ('23/10/26', 1, "", "", 'Ket had no high'),
    ('23/10/26', 5, "", "", ''),
    ('23/10/26', 1, "", "", 'Rapid delivery but I should have stuck with ketamine shards did not enjoy the himalayas ket blush not strong nor wonky bit of a let down to be honest ...'),
    ('22/10/26', 5, "", "", 'Excellent service. Quality product'),
    ('21/10/26', 5, "", "", 'ndd again top notch'),
    ('19/10/26', 5, "", "", ''),
    ('19/10/26', 5, "", "", 'Excellent edibles'),
    ('18/10/26', 5, "", "", '777 wonder stuff'),
    ('18/10/26', 5, "", "", 'Been using VIP a while now, everything is top quality,  beautiful smoke, power Edibles, and hash to knock ya socks off! Big up VIP everytime'),
    ('18/10/26', 5, "", "", 'Excellent from start to finish yet again. 24h delivery and Excellent product.  Xx'),
    ('17/10/26', 5, "", "", 'Arrived on a Saturday, paid hardly anything for 10/10 grade K. ‘VIP, a man you want on your team’✨'),
    ('16/10/26', 5, "", "", 'Amazing service, always on time always on point. I’ve tried one of the other vendors this week for the 1st, ordered Monday still not arrived and no re...'),
    ('16/10/26', 5, "", "", ''),
    ('16/10/26', 5, "", "", 'Spot on as usual'),
    ('15/10/26', 5, "", "", 'Ndd the best in the buisness'),
    ('15/10/26', 5, "", "", 'Everything landed all good , Ndd. Cheers 🍻'),
    ('15/10/26', 5, "", "", ''),
    ('14/10/26', 5, "", "", "Hash arrived ndd. Very sticky and hard to work with but a lovely smoke. Cheers V.I.P team can't fault you.👊"),
    ('14/10/26', 5, "", "", 'Great service and unbelievable prices! Love the packaging too very professional top vendor'),
    ('14/10/26', 5, "", "", 'Nice and smooth, light flavour. Hits well.'),
    ('13/10/26', 5, "", "", 'Great service, ordered Monday arrived Tuesday, as usual ❄️ top notch😉'),
    ('13/10/26', 5, "", "", 'Next day delivery and again top work from VIP. The queen snow is lovely. Smooth on the uptake and gives a nice long buzz so your not back in the bag e...'),
    ('13/10/26', 5, "", "", 'Ordered Friday Saturday delivered but wrong order so he sent it out Monday and arrived today every very good people vip!! Cheers vip.'),
    ('13/10/26', 5, "", "", 'Sensational'),
    ('12/10/26', 5, "", "", 'Kah-bling..DW so so lovely. Queen was nice too.'),
    ('12/10/26', 5, "", "", ''),
    ('12/10/26', 5, "", "", ''),
    ('11/10/26', 5, "", "", 'Ordered Sunday, landed Tuesday. The double washed is smooth and 🔥\nHighly recommended'),
    ('11/10/26', 5, "", "", 'That toad venom is off the scale it is fire!'),
    ('11/10/26', 5, "", "", 'Absolutely outstanding performance for the guys. Special NDD all the way.'),
    ('11/10/26', 5, "", "", 'Quality products, fast communication,  great prices,'),
    ('11/10/26', 5, "", "", 'Superb yet again. Ordered literally 20mins before the 1pm cut off. Recieved less than 24hrs later. Excellent service and top.quality ❄️. X'),
    ('11/10/26', 5, "", "", 'Very good brick well plesed womt be going anywhere else'),
    ('10/10/26', 5, "", "", ''),
    ('10/10/26', 5, "", "", ''),
    ('10/10/26', 5, "", "", ''),
    ('10/10/26', 5, "", "", "Nose beers were ace design. Didn't measure but seemed a bit low on liquid. Could maybe sort a better ratio. But quality stuff!\nThe double wash wa..."),
    ('10/10/26', 5, "", "", "Mostly all proper fire. A lot of people will claim this to be synthetic hash its not its commercial grade whole melt. Most hash folk don't have a..."),
    ('09/10/26', 5, "", "", 'Key lime and candy rain hash are piff, smoking clean as. Yet to try the flake. NDD will definitely be ordering again👍👊'),
    ('09/10/26', 5, "", "", 'Fucking fire as always thanks mate'),
    ('09/10/26', 5, "", "", ''),
    ('08/10/26', 5, "", "", '1st time ordering and impressed. Great communications, fast delivery and professional packing. Also great products and prices. Thank you'),
    ('07/10/26', 5, "", "", "2nd order from this vendor and I have to say I'm very happy with everything I've had so far. Vape tastes lovely and hits hard. Cheers dude, ..."),
    ('07/10/26', 5, "", "", 'Class service as usual, great product'),
    ('07/10/26', 5, "", "", ''),
    ('05/10/26', 5, "", "", ''),
    ('05/10/26', 5, "", "", 'PUMA 10/10'),
    ('05/10/26', 5, "", "", ''),
    ('04/10/26', 5, "", "", '10/10 stuff'),
    ('04/10/26', 5, "", "", 'Good gear,fast delivery'),
    ('04/10/26', 5, "", "", ''),
    ('03/10/26', 5, "", "", ''),
    ('03/10/26', 1, "", "", 'Defo was not animal minc. Was dry and hard to smoke.'),
    ('03/10/26', 5, "", "", 'top notch ndd 🥰'),
    ('02/10/26', 5, "", "", 'Always next day.. unbeatable prices top vendor'),
    ('02/10/26', 5, "", "", 'Easy to use bot and fast delivery. 10/10'),
    ('02/10/26', 5, "", "", 'Super fast delivery yet again and ❄️ is bloody lovely.  Best, most reliable vender on here by far . Cheers Emz x'),
    ('02/10/26', 5, "", "", 'Thank You! 🤝❤️\nNDD to NI.\nStealth Packaging.\nSuper Clean! ❄️\nVery Moorish! 🤤 \nSmooth and Tasty! 😃\nWould highly recommend.'),
    ('01/10/26', 5, "", "", 'Top class every order...thanks bro'),
    ('01/10/26', 5, "", "", "Ndd , nice product , can't go wrong for the price ! VIP all the way nice one 😁"),
    ('01/10/26', 5, "", "", "The best ket on the dw that's not in vials!\ncomms are spot on good honesty when thing go wrong they do don't chat shit like other vendors. W..."),
    ('30/09/26', 5, "", "", '5 start amazing products as always'),
    ('30/09/26', 5, "", "", ''),
    ('30/09/26', 5, "", "", 'clean high from their 🇲🇦 hash'),
    ('30/09/26', 5, "", "", 'Rapid delivery and product top notch ⭐⭐⭐⭐⭐'),
    ('29/09/26', 5, "", "", 'Nice hash'),
    ('29/09/26', 5, "", "", 'Top class price, rapid delivery, professional outfit these guys'),
    ('28/09/26', 5, "", "", 'Skip'),
    ('27/09/26', 5, "", "", ''),
    ('27/09/26', 5, "", "", "Can't complain wats so ever woe dis guy ordered o. Friday at 2pm had for Saturday 12:00\nI'm in Ireland too still NDD\nlisten bro thanks be lo..."),
    ('26/09/26', 5, "", "", 'Always banging can’t go wrong with VIP 👌🏽'),
    ('26/09/26', 5, "", "", "Ndd.awesome stealth, top egg at unbeatable price..I'll be back"),
    ('26/09/26', 5, "", "", 'Rolex is tops'),
    ('26/09/26', 5, "", "", "Really nice hash egg Toad venom. Decent sniff too. 24hr delivery and for the price you can't go wrong. Will definitely use this vendor again.👊"),
    ('25/09/26', 5, "", "", "Rolex brick is the best I've had in a while. Don't miss out on this!"),
    ('25/09/26', 5, "", "", 'Fast delivery, i was 42 pence short but all ok. Great vendor. The Rolex is extremely good'),
    ('25/09/26', 5, "", "", 'Fast and reliable all the time every time'),
    ('23/09/26', 5, "", "", 'Rapid delivery and quality product'),
    ('23/09/26', 5, "", "", 'Rolex 🔥 never expect anything less from vip'),
    ('22/09/26', 5, "", "", 'Less than 24hrs between order and land. Top quality every time. That’s VIP ⭐️'),
    ('22/09/26', 5, "", "", '7g 00. (Was 7.25g TY❤️)\nNDD to NI.\nProduct Vac Sealed, in a small Box and then Envelope.\n(I really appreciate the Stealth Packaging! \nThank you. 💚)\nVe...'),
    ('20/09/26', 5, "", "", ''),
    ('19/09/26', 5, "", "", 'Banging service everytime! Candy Rain is sticky and potent!'),
    ('19/09/26', 5, "", "", ''),
    ('19/09/26', 5, "", "", 'Wow very nice all of them!'),
    ('18/09/26', 5, "", "", 'Top notch as usual'),
    ('18/09/26', 5, "", "", 'Ndd packed well, ket is loverly smells old skool and took 20mins to kick in, hash bubbles smokes nice, coke is dry looks like end of the brick good fo...'),
    ('18/09/26', 5, "", "", 'Excellent service once again. Thanks 💚'),
    ('18/09/26', 5, "", "", 'Arrived quick cheers also great communication top service'),
    ('17/09/26', 5, "", "", ''),
    ('17/09/26', 5, "", "", 'Never had an issue , quality service ⭐⭐⭐⭐⭐'),
    ('16/09/26', 5, "", "", 'Easy transaction and great updates from the crew. 777 ❄️ great!'),
    ('16/09/26', 5, "", "", ''),
    ('16/09/26', 5, "", "", "I didn't know cocaine can be this good, clean high, clean on the nose and extremely euphoric."),
    ('15/09/26', 5, "", "", 'always good stuff reliable and quick 👍'),
    ('15/09/26', 5, "", "", 'Landed next day can’t complain at all'),
    ('14/09/26', 5, "", "", 'Peng as always fire 🔥'),
    ('14/09/26', 5, "", "", ''),
    ('14/09/26', 5, "", "", "Rapid delivery's fire products even got an extra gram this time well run business never any issues and if ther is the lads have it sorted in no t..."),
    ('13/09/26', 5, "", "", 'Quick, tracked delivery.\nThe double washed is superb, really clean, great product and great vendor!'),
    ('12/09/26', 5, "", "", ''),
    ('12/09/26', 5, "", "", 'Legit and fast'),
    ('12/09/26', 5, "", "", ''),
    ('12/09/26', 5, "", "", 'NDD. The Candy Gelato flower is banging! Lovely candy terps. And kosha kush hash again pure fire. One of the top vendors on tbc!'),
    ('12/09/26', 5, "", "", 'Order landed quick, both the micron logic hash, and fu cone high edibles were absolutely fire 🔥 never been disappointed by vip!'),
    ('12/09/26', 5, "", "", ''),
    ('12/09/26', 5, "", "", 'double zero hash is strong and tastes really good. First order of probably many'),
    ('12/09/26', 5, "", "", 'Spot on as always cheers guys'),
    ('11/09/26', 5, "", "", ''),
    ('11/09/26', 5, "", "", 'Fantastic service ordered  after 5pm ! Came the next day !'),
    ('11/09/26', 5, "", "", 'VIP I’ve used for several years now and everything I’ve ever ordered is exceptional.'),
    ('10/09/26', 5, "", "", 'stuff comes very fast n always good quality 🙌🙌'),
    ('10/09/26', 5, "", "", "NDD once again Thank you, LPC Eggs look amazing and I'm sure theyre 10/10.. Appreciate yas!!!"),
    ('09/09/26', 5, "", "", 'Best edibles I’ve ever had ! Actually 1000mg as sated ! # Big up the VIP TEAM :)'),
    ('08/09/26', 5, "", "", 'Spectacular always on point lads at vip legendary characters'),
    ('08/09/26', 5, "", "", 'Rapid delivery.'),
    ('06/09/26', 5, "", "", 'super smooth'),
    ('06/09/26', 5, "", "", 'Honestly was told by my doctor years ago i had untreatable depression and anxiety they gave me every drug possible and that only sugar coated it and m...'),
    ('04/09/26', 5, "", "", 'Great service'),
    ('04/09/26', 5, "", "", '777 brick is amazing,  super clean, strong as fuck it hits like a train, aslo washed back perfect! Always amazing products wouldnt go else where !'),
    ('03/09/26', 4, "", "", 'Quick delivery,not bad for price but not high grade,possibly cali mids imo'),
    ('03/09/26', 5, "", "", 'The ❄️ is fire. Definitely worth ordering and arrived next day'),
    ('03/09/26', 5, "", "", 'Absolutely beautiful, you guys get it 🤘'),
    ('02/09/26', 5, "", "", "Excellent service. Next day delivery and great ❄️ you won't regret buying"),
    ('02/09/26', 5, "", "", 'Perfect ❄️ great price'),
    ('29/08/26', 5, "", "", 'Excellent quality and service as usual, thanks guys ❤️'),
    ('29/08/26', 5, "", "", 'Can’t go wrong with VIP'),
    ('28/08/26', 5, "", "", 'Absolute bangers'),
    ('28/08/26', 5, "", "", 'NDD to NI, appreciate ya following the notes too mate, thank you!!!'),
    ('28/08/26', 5, "", "", ''),
    ('28/08/26', 5, "", "", "AWESOME! I ordered on Wednesday and it came Friday. Really nice device, works really well and is a very smooth and doesn't make me cough. I like ..."),
    ('27/08/26', 5, "", "", 'Great value'),
    ('27/08/26', 5, "", "", 'Excellent quality and service as always'),
    ('26/08/26', 5, "", "", ''),
    ('26/08/26', 5, "", "", '5* product 5* stealth 5* delivery time.'),
    ('26/08/26', 5, "", "", '5* product 5* stealth 5* delivery time.'),
    ('26/08/26', 5, "", "", ''),
    ('25/08/26', 5, "", "", 'I’m fu cone high are absolutely bang on. Ordering some more as I type. Keep up the good work. \n\nThe Don Julio on the other hand. I keep it positive bu...'),
    ('25/08/26', 5, "", "", ''),
    ('25/08/26', 5, "", "", ''),
    ('25/08/26', 5, "", "", ''),
    ('25/08/26', 5, "", "", 'Peng'),
    ('25/08/26', 5, "", "", ''),
    ('24/08/26', 5, "", "", 'Really well packaged. Love the design. Love the taste. Love the experience. Big up!'),
    ('24/08/26', 5, "", "", 'The chocolate cones are lovely nice and strong. Went well after the DW. DW is clean as 10/10  ❤️👌. VIP WEEKEND.'),
    ('24/08/26', 5, "", "", 'Quality fast delivery'),
    ('24/08/26', 5, "", "", 'Budder tastes amazing'),
    ('23/08/26', 5, "", "", 'El Ray is absolutely fire , clean sniff and washes back perfect'),
    ('21/08/26', 5, "", "", ''),
    ('21/08/26', 5, "", "", 'Amazing quality very reliable vendor'),
    ('21/08/26', 5, "", "", 'Delivered next day T24. Edibles look great.'),
    ('21/08/26', 5, "", "", 'Rapid'),
    ('20/08/26', 5, "", "", 'Love this vendor. Just received 7g mushroom tops 🍄  😋'),
    ('20/08/26', 5, "", "", 'Best in the game'),
    ('20/08/26', 5, "", "", 'Absolute top vendor. NDD was spot on. Decent gear too!'),
    ('20/08/26', 5, "", "", ''),
    ('19/08/26', 5, "", "", 'These guys are the real deal'),
    ('18/08/26', 5, "", "", 'C is fire as usual never fails to impress🔥. Edibles are banging too. VIP team thank you for all the work you do appreciate you guys 🙏🏼'),
    ('18/08/26', 5, "", "", 'quick delivery good stuff would recommend to all 🔥'),
    ('18/08/26', 5, "", "", 'Will be ordering again easy money maker or a nice smoke right head banger knocks me out ⭐️⭐️⭐️⭐️⭐️'),
    ('15/08/26', 5, "", "", 'Top quality as always'),
    ('15/08/26', 5, "", "", 'Just like the brave 300, all gas no brakes. 10/10 would fight some Persians 🔊🙌'),
    ('15/08/26', 5, "", "", 'Can’t go wrong speedy delivery and best prices gorgeous hash 💯🤤'),
    ('15/08/26', 5, "", "", 'Next day even up to the highlands. Well packaged and banging gear 👌🏻'),
    ('15/08/26', 5, "", "", ''),
    ('14/08/26', 5, "", "", 'ordered just before 2pm and still got it next day! i dont get how many other vendors are struggling with royal mail but these just fly'),
    ('14/08/26', 5, "", "", 'Sooo good!'),
    ('14/08/26', 5, "", "", 'Absolutely top class. Delivery is rapid and products are top notch. XxEmzxx'),
    ('14/08/26', 5, "", "", 'Reliable as always, 10/10 no point in going local anymore'),
    ('14/08/26', 5, "", "", 'Landed in 24 hours to NI, perfect service perfect product, Candy Rain Piatella looks phenomenal 😍'),
    ('13/08/26', 5, "", "", 'Top shelf quality , top shelf service , you guys are crushing it 👊'),
    ('13/08/26', 5, "", "", 'Quick and discreet'),
    ('13/08/26', 5, "", "", ''),
    ('13/08/26', 5, "", "", 'Hash is truly exceptional, every single one that was sent, the Gelato is like budder u need a dabber for it. My favourite is the key lime though, just...'),
    ('11/08/26', 5, "", "", ''),
    ('10/08/26', 5, "", "", 'Super quick delivery and proper quality ❄️, will be ordering again👌'),
    ('10/08/26', 5, "", "", 'Order a few times now spot on every time& arrives when expected'),
    ('08/08/26', 5, "", "", 'Fast shipping'),
    ('08/08/26', 5, "", "", 'Superb as always. Tracked from start to finish.  ❄️ spot on a usual. Thanks guys  xx'),
    ('06/08/26', 5, "", "", "3rd order now , very pleased with the quality of the coke, those Rolex pills and 2cbs I had last time were spot on too! Can't fault this guy !"),
    ('06/08/26', 5, "", "", 'never fails on the NDD, even on weekends, ordered just before 2pm cut off & still arrived before 11am! used many times and till deffos continue'),
    ('05/08/26', 5, "", "", '2nd time ordering can’t go wrong lovely stuff 👌🏽proper tasty and a head banger  🤤🤯'),
    ('05/08/26', 5, "", "", 'Always NDD and always, ALWAYS exceptional quality cocaine ❄️'),
    ('05/08/26', 5, "", "", 'Overalll great vendor. Quick responses and the product is spot on'),
    ('05/08/26', 5, "", "", 'Place my order at 12:30 on Tuesday and order landed at 11 am on the Wednesday all good received ate 10/10'),
    ('04/08/26', 5, "", "", ''),
    ('04/08/26', 5, "", "", 'Next day and super strong what a legend'),
    ('04/08/26', 5, "", "", ''),
    ('02/08/26', 5, "", "", 'Outstanding service as usual - quick no nonsense delivery and great stuff'),
    ('02/08/26', 5, "", "", 'Class utter class'),
    ('01/08/26', 3, "", "", 'Was okey wouldn’t say was top top'),
    ('01/08/26', 5, "", "", 'El rey top quality 👌'),
    ('01/08/26', 5, "", "", 'Thanks to the team that makes it happen'),
    ('31/07/26', 5, "", "", 'Extremely fast delivery and tracking given from the start (perfect). Excellent product 👌.  One of the best sellers on here. cheers pal Emz x'),
    ('31/07/26', 5, "", "", 'ordered 10 got 25, everyone loves the dragonballZ prints on them n they r amazinggg quality definitely b getting all tabs from here in future 🙏🙏🙏'),
    ('30/07/26', 5, "", "", 'Strong,good quality and nice flavor.'),
    ('30/07/26', 5, "", "", 'Try it for yourself, no words can explain.'),
    ('29/07/26', 5, "", "", ''),
    ('29/07/26', 5, "", "", 'First and foremost, order Monday arrived Tuesday… love the fact the order came with the tracking! oh and the gear  is quality😉'),
    ('29/07/26', 5, "", "", 'Even after cut off landed next day. That ☘️ ❄️ is ⚡️'),
    ('29/07/26', 3, "", "", "The speed was amazing. The 2cb didn't feel like the labeled strength, it was quite weak. The edibles were half out of their packaging and didn&#0..."),
    ('28/07/26', 5, "", "", 'Quality gear. Delivered next day'),
    ('28/07/26', 5, "", "", 'Quality gear. Delivered next day'),
    ('28/07/26', 5, "", "", 'Quality gear. Delivered next day'),
    ('25/07/26', 5, "", "", 'NDD, Great packing,  and absolutely banging piatella, Kosha Kush is straight 🔥'),
    ('25/07/26', 5, "", "", 'Fire'),
    ('25/07/26', 5, "", "", ''),
    ('25/07/26', 5, "", "", ''),
    ('25/07/26', 5, "", "", ''),
    ('25/07/26', 5, "", "", ''),
    ('25/07/26', 5, "", "", ''),
    ('25/07/26', 5, "", "", 'Not tried the speed had a lil try of the Irish and wow speechless'),
    ('23/07/26', 5, "", "", 'Slight delay (RM’s fault) - Quality stuff 🔥'),
    ('23/07/26', 5, "", "", 'NDD, bang on weight, and beautiful quality.'),
    ('22/07/26', 5, "", "", 'Great service, NDD, Stealth packing.Candy rain Piatella is absolute fire 🔥, toad venom olive good smoke, nice burn'),
    ('22/07/26', 5, "", "", 'All are amazing quality 10/10 👌🏽 would recommend beautiful taste and smell 🤤'),
    ('22/07/26', 5, "", "", 'Lovely stuff came next day'),
    ('21/07/26', 5, "", "", 'The item was missing from the postal order but was sent out the next working day which is unfortunate over a weekend but arrived sharp and sent out sp...'),
    ('20/07/26', 5, "", "", 'Awesome again, the double washed is top notch and the Luck of the Irish was bloody special, best of a few top end ones I have had lately. Mad respect ...'),
    ('18/07/26', 5, "", "", ''),
    ('17/07/26', 5, "", "", "Ka-bling , DW is lovely but it is a creeper so don't be fooled by the fact it goes up your nose like air......it will catch up to you. Clean as f..."),
    ('17/07/26', 5, "", "", 'Awesome vendor, fast delivery and quality product, the double washed is the one for sure 💪🏼'),
    ('17/07/26', 5, "", "", 'Pack landed cheers vip banging stuff'),
    ('16/07/26', 5, "", "", "That DW ❄️ is up there with the best yet 💥🔥🔥\nIt's like being Tangoed by Snortnoy's big walloper 🤯 🍆  Pornographic powder 😈"),
    ('16/07/26', 5, "", "", 'Order yesterday and landed today. Packaging is superb and the product is even better. Thanks team, will 100% be a returning customer'),
    ('16/07/26', 5, "", "", 'Fire!'),
    ('15/07/26', 5, "", "", 'Normal delivery arrived next day for me. Blue brick is super gear. Nice pleasant smell, nice high from it. \n\nPackaging as always super secure.'),
    ('12/07/26', 5, "", "", 'The gelato 33 and piatella is fire 🔥'),
    ('12/07/26', 5, "", "", '⭐⭐⭐⭐⭐'),
    ('12/07/26', 5, "", "", ''),
    ('12/07/26', 5, "", "", ''),
    ('11/07/26', 5, "", "", ''),
    ('11/07/26', 5, "", "", 'Amazing service as always'),
    ('11/07/26', 5, "", "", 'Brilliant delivery time ordered and delivered within 22 hours'),
    ('10/07/26', 5, "", "", 'Extremely fast delivery.  Product spot on 👌 xx'),
    ('10/07/26', 5, "", "", 'Faultless service guys'),
    ('09/07/26', 5, "", "", 'Absolute 🔥'),
    ('09/07/26', 5, "", "", '❄️ and elite vape both 10/10, landed within 24h of ordering'),
    ('08/07/26', 5, "", "", 'first time trying nose beers  snd vendor  and wow bang on both of them and 10.10 stuff 💥💥💥💥'),
    ('07/07/26', 5, "", "", 'Always VIP service. Stamp hit like a left hook 🥊'),
    ('07/07/26', 5, "", "", '2cb 10/10'),
    ('07/07/26', 5, "", "", 'Bricks always fire 🔥'),
    ('06/07/26', 5, "", "", 'Utter class'),
    ('04/07/26', 5, "", "", ''),
    ('03/07/26', 5, "", "", 'Top products and service as always. Wouldn’t use anyone else. Thanks VIP team👌🏼'),
    ('03/07/26', 5, "", "", 'Smells like fire 🔥!!'),
    ('03/07/26', 5, "", "", 'NDD. 🔥 stuff'),
    ('02/07/26', 5, "", "", 'That Chapo 💥🔥🔥🤯🤯🤯\n\nA+++ vendor 👌'),
    ('02/07/26', 5, "", "", 'Elite level service 👊👊'),
    ('01/07/26', 5, "", "", 'Good quality and quick delivery. 5⭐️'),
    ('01/07/26', 5, "", "", 'Both products and service are 10/10 nose beers are handy and strong ! Much easier on the nose, Diaz are genuine 10mg Martin dows'),
    ('30/06/26', 5, "", "", 'Top notch and quick as fuck. Nailed it again guys ❤️'),
    ('30/06/26', 5, "", "", 'NDD. Thanks mate'),
    ('30/06/26', 5, "", "", 'That DW AoS ♠️  is up there as the cleanest ❄️ yet. \n\nVery smooth 👌 but defo not a creeper 💥🔥🔥 🤯🤯\n\nA+++ vendor 😎'),
    ('30/06/26', 5, "", "", 'Great first order with this vendor. Everything is top notch, I will be ordering again. \nThe work is of the highest quality for sure. Thanks VIP team, ...'),
    ('30/06/26', 5, "", "", 'Easy to order and great products'),
    ('27/06/26', 5, "", "", 'NDD again as always and spot on gear as usual can’t fault these guys'),
    ('27/06/26', 5, "", "", 'Fast delivery, came next day. The hash is the bang bang 🔥🔥 too sexy'),
    ('27/06/26', 4, "", "", 'I’m gonna be real, receiving Piatella in a disproportionately HUGE baggie in the big 2026 just feels very wrong unless it’s a bulk brick of it. Piatel...'),
    ('27/06/26', 5, "", "", ''),
    ('27/06/26', 5, "", "", 'another speedy delivery, really nice k too, positive stuff, does exactly what it says on the tin'),
    ('27/06/26', 5, "", "", 'Just dropped this morning excellent service as usual, quality of the ket is banging. Well worth the money 😁'),
    ('26/06/26', 5, "", "", 'Slight issue resolved immediately, everything these guys sell is 10/10 highly recommend'),
    ('26/06/26', 4, "", "", "Would have left 5 stars. The only problem I had was knowing what strength the LSD was and the strength of the xtc rolex's"),
    ('25/06/26', 5, "", "", 'Top shelf thanks vip 👍🏾'),
    ('25/06/26', 5, "", "", 'I had the AoS DW and it blew me away. Absolute rocket fuel ❄️'),
    ('25/06/26', 5, "", "", 'Kings of the game ppl\nalready know'),
    ('25/06/26', 5, "", "", 'Quality product arrived quick thank you.'),
    ('25/06/26', 5, "", "", 'Got an amazing deal on an amazing product the nose beers are perfect for a discrete night out any where'),
    ('25/06/26', 5, "", "", 'Wow some of the best looking and smelling hash ive ever seen, weighed 2g over as well. Thank you very much'),
    ('25/06/26', 5, "", "", 'Package came ndd stuff was fire had a slight issue communication was perfect sent out ndd and even added extra pingers definitely a vendor to follow'),
    ('25/06/26', 5, "", "", ''),
    ('24/06/26', 5, "", "", 'A really good tasting vape, super fast delivery… I recommend 10/10 these guys are the best 💯'),
    ('23/06/26', 5, "", "", 'Quick delivery good product can’t be faulted 👌'),
    ('23/06/26', 5, "", "", 'ndd, first time ordering, nice bot, looking forward to trying it out. the reviews give it high standards'),
    ('22/06/26', 5, "", "", 'Stunning bit of work as always'),
    ('22/06/26', 5, "", "", 'Absolutely banging bit of DW'),
    ('21/06/26', 5, "", "", 'Great products excellent service 👊🔥'),
    ('19/06/26', 5, "", "", "Lovely taste, only problem I've found so far is some USB C cables don't seem to charge it. Looks like a size thing but idk? \n\nCrazy good for..."),
    ('18/06/26', 5, "", "", 'Noice'),
    ('18/06/26', 5, "", "", 'Double washed ❄️ definitely worth the small extra, nice and smooth. Easy ordering process via the bot, fast discrete delivery. Top man, thanks ✌️'),
    ('18/06/26', 5, "", "", 'Insanely fast delivery and service thank you'),
    ('18/06/26', 5, "", "", ''),
    ('17/06/26', 5, "", "", 'Came less than 24hours after order, great vendor'),
    ('17/06/26', 5, "", "", 'That DW 👑👑 ❄️ was some of the cleanest yet 👌 A creeper 💥🔥🔥\n\nA+++ vendor'),
    ('17/06/26', 5, "", "", 'Postage - excellent .\nCustomer service - Tremendous. \nDouble wash - SILK'),
    ('16/06/26', 5, "", "", 'VIP  never let you down, 5*all the way, products always Top shelf with NDD\n🫡'),
    ('14/06/26', 5, "", "", 'All bang on as usual'),
    ('13/06/26', 5, "", "", ''),
    ('13/06/26', 5, "", "", ''),
    ('13/06/26', 5, "", "", 'That DW is the best stuff to grace my nostrils! 😆 not tried the mongstix yet 👌'),
    ('13/06/26', 5, "", "", 'Sent Friday and Saturday afternoon pack landed. Top quality product and the VIP service you deserve ⭐️'),
    ('12/06/26', 5, "", "", 'Excellent as always'),
    ('12/06/26', 5, "", "", '2CB rockets are 10/10 quality.'),
    ('11/06/26', 5, "", "", ''),
    ('10/06/26', 5, "", "", ''),
    ('10/06/26', 5, "", "", ''),
    ('09/06/26', 5, "", "", ''),
    ('09/06/26', 5, "", "", 'Rapid delivery with discreet packaging, contents look wicked too. 11/10 definitely be back🔥'),
    ('09/06/26', 5, "", "", '🔥'),
    ('09/06/26', 5, "", "", 'Less than 24hrs between order and delivery! Proper rapid. The double wash is excellent, long lasting smooth buzz. Easy on the bugle as well. I asked f...'),
    ('07/06/26', 5, "", "", 'Arrived fast and packaged perfectly'),
    ('06/06/26', 5, "", "", 'My cousin got her speed today ylthe guy where good enough to send it special delivery she over the moon says stuff fire 🔥🔥🔥'),
    ('06/06/26', 5, "", "", 'Ridiculous bit of tackle. Strong af x'),
    ('06/06/26', 5, "", "", ''),
    ('06/06/26', 5, "", "", 'Vapes are 10/10'),
    ('06/06/26', 5, "", "", ''),
    ('05/06/26', 5, "", "", 'NDD as always, USDT and USD1 both bangin 👌🏻'),
    ('05/06/26', 5, "", "", 'The Elites vapes are amazing. The DW is so clean. Perfect vendor, everything top tier'),
    ('05/06/26', 5, "", "", 'VIP are the best vendors by far,\nProducts are Top Gear,\nWith NDD great Comms, keep up the good work boys\n❤️'),
    ('05/06/26', 5, "", "", 'Always on it you lads! Top Tier service and even better product ! 🫡'),
    ('05/06/26', 5, "", "", ''),
    ('04/06/26', 5, "", "", '1st order with VIP and very impressed. NDD, stealth on point. As for the nose beer, wow. Aesthetically absolute class and as for the product itself, b...'),
    ('04/06/26', 5, "", "", ''),
    ('04/06/26', 5, "", "", 'Skip'),
    ('03/06/26', 5, "", "", 'Asked for 007 but got Jungle, sure it will be just as good.  Happy with the speed of delivery and great products'),
    ('02/06/26', 5, "", "", 'Best stuff going, very clean energetic buzz. Nose beers are good for discrete use'),
    ('02/06/26', 5, "", "", 'Fast delivery, great stuff!'),
    ('02/06/26', 5, "", "", 'Went crazy and hit multiple licks on Tesco 10/10 🔥'),
    ('02/06/26', 5, "", "", 'Great Value, tastes amazing and good packaging. 🔥🔥'),
    ('01/06/26', 5, "", "", 'NDD, vapes 🔥 once again,and the gear, Thanks again VIP'),
    ('01/06/26', 5, "", "", 'NDD , great stuff, great service'),
    ('01/06/26', 5, "", "", 'Rapid delivery on point'),
    ('31/05/26', 5, "", "", 'NDD AND BANGING COKE'),
    ('31/05/26', 5, "", "", 'NDD and as usual top gear'),
    ('29/05/26', 5, "", "", 'Top service as usual guys. Quality of product is lush aswell. Thanks guys 😁'),
    ('27/05/26', 5, "", "", 'Very fucking nice and service excellent as usual, thank you ❤️'),
    ('27/05/26', 5, "", "", 'Next day delivery and good packaging cant really ask for more 🤷🏻\u200d♂️'),
    ('27/05/26', 5, "", "", ''),
    ('24/05/26', 5, "", "", 'Top teir'),
    ('24/05/26', 5, "", "", 'Proper gear none of that twitchy paranoia straight clean stuff💯'),
    ('24/05/26', 5, "", "", '😂 good service, I thought it was weak cause it wernt hitting ain’t done over a year or two but I was wrong it creeped on me and this shit was fire 🔥'),
    ('23/05/26', 5, "", "", 'Banging🔥 thank you'),
    ('23/05/26', 5, "", "", 'NDD and fucking beautiful as always well done team'),
    ('22/05/26', 5, "", "", 'Unbelievably fast delivery, bare in mind I placed order 1 day before and vendor reassured me it will arrive. Next day… It lands!\n\nGreat colo yola, rec...'),
    ('22/05/26', 5, "", "", 'Much appreciated, these were 👍👍'),
    ('21/05/26', 5, "", "", 'Mongsticks are banging hit hard 👍. DW so good back for more. VIP your one stop shop for all. ❤️❤️'),
    ('21/05/26', 5, "", "", '🤯🤯 💥🔥🔥🔥\n\nA+++ vendor.'),
    ('20/05/26', 5, "", "", 'Quick and goooood.'),
    ('17/05/26', 5, "", "", ''),
    ('16/05/26', 5, "", "", 'Best I’ve had in years. NDD top vendor'),
    ('15/05/26', 5, "", "", 'Good communication, fast shipping & great product!'),
    ('15/05/26', 5, "", "", 'Arrived on time the top teir service'),
    ('15/05/26', 5, "", "", 'This stuff is very good for the price.'),
    ('14/05/26', 5, "", "", 'NDD as usual . This shit is fire already ordered more. Big up vip'),
    ('13/05/26', 5, "", "", 'That 🇵🇪 is a banger 💥🔥🔥🤯\n\nA+++ vendor 👌👍👍'),
    ('12/05/26', 5, "", "", 'Landed as perusal top vendor top teir gear'),
    ('12/05/26', 5, "", "", 'Good service, responded to questions 👍🏼'),
    ('12/05/26', 5, "", "", 'Come fast best service about 10/10'),
    ('10/05/26', 5, "", "", 'Perfection'),
    ('10/05/26', 5, "", "", 'Not a commercial chemically over powering head banger brick at all, its a long lasting smooth creamy high with a pleasant  taste- you know its good wh...'),
    ('09/05/26', 5, "", "", 'Excellent quality and service. I was slightly inpatient as orders came separately, so make sure you stay patient. Communication was incredibly good an...'),
    ('09/05/26', 5, "", "", 'The shard is excellent and the 2 cbs are lush and clean ❤️'),
    ('08/05/26', 5, "", "", 'Top teir gear top team go over and above to keep vip group happy'),
]

# See the note above the Total/Monthly/Weekly block: this has to be defined
# after ALL_REVIEWS, and has to stay at or above its length.
BASE_TOTAL = len(ALL_REVIEWS)

STAR_DIST = {s: sum(1 for r in ALL_REVIEWS if r[1] == s) for s in range(1, 6)}


def _esc(s):
    """Escape user-supplied text for parse_mode="HTML" - same rule main.py's
    own _esc() uses: only & < > are special; an apostrophe is left alone, or
    Telegram prints it back as the literal &#x27; instead of decoding it."""
    return html.escape(str(s or ""), quote=False)


def _product_words(prefix):
    """"flower_runtz" -> ["runtz"]; drops short/generic pieces like "p" or "cat"."""
    return [w for w in prefix.lower().split("_") if len(w) >= 4]


def filter_reviews(prefix=None, stars=None, source=None):
    """Reviews matching a product and/or a star rating, order preserved.

    `source` is the already-merged DB-plus-backlog list reviews_content()
    builds; this never reads ALL_REVIEWS directly so it works the same way
    on either.

    Matching a product is best-effort: there is no shared table between a
    review's free-text `products` field and a catalogue prefix like
    "flower_runtz", so this checks whether a meaningful word from the
    prefix turns up in that text. It will miss a review whose product name
    shares no word with its prefix (prefix "cok", product "CStilton" is one
    such case in this catalogue) - there is no way to match those without
    either the admin typing the prefix itself or a prefix-to-name table
    someone writes by hand.
    """
    rows = ALL_REVIEWS if source is None else source
    if prefix:
        words = _product_words(prefix)
        rows = [r for r in rows
                if words and any(w in (r[3] or "").lower() for w in words)]
    if stars:
        rows = [r for r in rows if r[1] == stars]
    return rows


def format_review(review):
    """One review's display block: date, stars, amount, product, the words."""
    date_str, stars, amount, products, text = review
    stars = max(0, min(5, int(stars or 0)))
    head = f"{_esc(date_str)}  {'★' * stars}{'☆' * (5 - stars)}"
    if amount:
        head += f" — {_esc(amount)}"
    lines = [head]
    if products:
        lines.append(_esc(products))
    lines.append(f"↷ {_esc(text)}" if text else "↷ <i>No comment</i>")
    return "\n".join(lines)
