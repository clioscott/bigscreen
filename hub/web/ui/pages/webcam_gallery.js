// Ready-made live webcams for the Webcams page (all 24/7 YouTube live streams, checked live 2026-10-18; Mt Seymour,
// White Rock Pier and Tofino Harbour need deno on the hub, see hub/webcams.py deno_path).
// A stream that restarts gets a new video id: if one stops working, Test on its page says so, and the channel
// usually has the new one under /streams.
const yt = (id) => ({ type: "youtube", url: `https://www.youtube.com/watch?v=${id}` });
const VAN = { tz: "America/Vancouver" };

export const GALLERY = [
  { group: "Downtown & harbour", icon: "Building2", cams: [
    { id: "DhkGXCgqWjg", label: "Vancouver Harbour", place: "Burrard Inlet & North Shore", credit: "Vancouver City Views", note: "Canada Place, the harbour and the North Shore mountains" },
    { id: "rxyNjFKwzJA", label: "Downtown Vancouver", place: "Skyline & viaducts", credit: "Vancouver City Views", note: "Downtown towers, Canada Place and the viaducts; the camera moves between views" },
    { id: "dtggqG3Xe6k", label: "Canada Place", place: "Vancouver Harbour", credit: "Vancouver Port Live", note: "Harbour traffic in front of the skyline, from the North Shore" },
    { id: "aq8s2lIbpCQ", label: "Skyline Panorama", place: "Vancouver", credit: "Vancouver Port Live", note: "Wide view of the city across the inlet; good at sunset" },
    { id: "GHEmhcWjiTE", label: "Harbour Seaplanes", place: "Coal Harbour", credit: "Vancouver Port Live", note: "Seaplanes and ships against the skyline" },
    { id: "A-aYQjV2AJ4", label: "False Creek", place: "Yaletown", credit: "Vancouver Yaletown Live", note: "Rotates between False Creek, the seawall and the skyline" },
    { id: "Mm_KgjdmWUc", label: "False Creek Skyline", place: "Yaletown", credit: "Vancouver Yaletown Live", note: "Marinas and towers along False Creek" },
    { id: "aByyCUuaI0Q", label: "Cambie Bridge", place: "False Creek", credit: "Vancouver Yaletown Live", note: "Bridge traffic and boats" },
    { id: "gEVhrDQc8C0", label: "BC Place", place: "Downtown Vancouver", credit: "Billyforce", note: "The stadium and Pacific Boulevard from above" },
    { id: "EGL7lTaxNcA", label: "Street View", place: "Vancouver", credit: "Vancouver Live Channel", note: "180° fisheye of a downtown street" },
  ]},
  { group: "Beaches & sunsets", icon: "Sunset", cams: [
    { id: "ClvJjIz5GE4", label: "White Rock East Beach", place: "Semiahmoo Bay", credit: "City of White Rock", note: "The bay, the pier and the mountains across the border" },
    { id: "JZ3UV60ykqc", label: "Long Beach", place: "Tofino", credit: "Long Beach Lodge", note: "Open Pacific surf beach" },
    { id: "Tbt6_787CB8", label: "Cox Bay", place: "Tofino", credit: "Pacific Sands", note: "Surf and sand, HD" },
    { id: "LqaP8m2OIqM", label: "Cox Bay Wide", place: "Tofino", credit: "Pacific Sands", note: "The whole bay from the resort" },
    { id: "4MK3E9EWDSY", label: "White Rock Pier", place: "White Rock", credit: "City of White Rock", note: "The promenade and the long pier out into the bay" },
    { id: "6-cLoX0odoc", label: "Big Beach", place: "Ucluelet", credit: "UkeeTube", note: "Rocky shore through the trees, sunrise to sunset" },
    { id: "sLixr8cThJo", label: "Ross Bay", place: "Victoria", credit: "Ross Bay Live Webcam", note: "Looking out over the Strait of Juan de Fuca" },
  ]},
  { group: "Mountains & snow", icon: "MountainSnow", cams: [
    { id: "vLawo-FrBKk", label: "Mystery Peak", place: "Mt Seymour", credit: "Mt Seymour", note: "Alpine meadow and the peaks beyond, from the top of the ski area" },
    { id: "yQdKq9NuEa0", label: "Snow Stake", place: "Mt Seymour", credit: "Mt Seymour", note: "The Stoke-O-Meter snow board: how much fell overnight" },
  ]},
  { group: "Water & wildlife", icon: "Fish", cams: [
    { id: "9mg9PoFEX2U", label: "Sea Otters", place: "Vancouver Aquarium", credit: "Vancouver Aquarium", note: "The otter pool, from above" },
    { id: "_KXHUb0wFRE", label: "Sea Otters (cam 2)", place: "Vancouver Aquarium", credit: "Vancouver Aquarium", note: "The otters' rocky corner" },
    { id: "_qN4aao5kWc", label: "Tofino Harbour", place: "Tofino", credit: "Tofino Sea Kayaking", note: "Docks, boats and the mountains of Clayoquot Sound" },
    { id: "gUR8_ZvxdHA", label: "Saturna Island", place: "Gulf Islands", credit: "Randy Mennie", note: "Ships passing in Boundary Pass" },
    { id: "VF2WmFgrtGg", label: "Departure Bay", place: "Nanaimo", credit: "Departure Bay LIVE", note: "BC Ferries coming and going" },
    { id: "MaYMrtIXxvA", label: "Cadboro Bay", place: "Victoria", credit: "Royal Victoria Yacht Club", note: "Marina and bay, wide angle" },
  ]},
  { group: "Further afield", icon: "Globe", cams: [
    { id: "dfVK7ld38Ys", label: "Shibuya Crossing", place: "Tokyo, Japan", credit: "FNN", tz: "Asia/Tokyo", note: "The scramble crossing; busiest in Tokyo's evening (your early morning)" },
  ]},
].map((g) => ({ ...g, cams: g.cams.map((c) => ({ ...VAN, ...c, source: yt(c.id) })) }));

// a real current frame, grabbed by the hub; the channel's own thumbnail (often artwork) if that fails
export const snap = (src) => `/api/webcams/snapshot.jpg?type=${encodeURIComponent(src.type || "stream")}&url=${encodeURIComponent(src.url)}`;
export const thumb = (c) => `/api/webcams/thumb/${c.id}.jpg`;
