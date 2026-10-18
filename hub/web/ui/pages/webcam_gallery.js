// Ready-made live webcams for the Webcams page (all 24/7 YouTube live streams, checked live 2026-10-18; Mt Seymour,
// White Rock Pier and Tofino Harbour need deno on the hub, see hub/webcams.py deno_path).
// A stream that restarts gets a new video id: if one stops working, Test on its page says so, and the channel
// usually has the new one under /streams.
const yt = (id) => ({ type: "youtube", url: `https://www.youtube.com/watch?v=${id}` });
const VAN = { tz: "America/Vancouver" };

export const GALLERY = [
  { group: "Downtown & harbour", icon: "Building2", cams: [
    { id: "DhkGXCgqWjg", lat: 49.2888, lon: -123.1111, label: "Vancouver Harbour", place: "Burrard Inlet & North Shore", credit: "Vancouver City Views", note: "Canada Place, the harbour and the North Shore mountains" },
    { id: "rxyNjFKwzJA", lat: 49.2827, lon: -123.1207, label: "Downtown Vancouver", place: "Skyline & viaducts", credit: "Vancouver City Views", note: "Downtown towers, Canada Place and the viaducts; the camera moves between views" },
    { id: "dtggqG3Xe6k", lat: 49.2888, lon: -123.1111, label: "Canada Place", place: "Vancouver Harbour", credit: "Vancouver Port Live", note: "Harbour traffic in front of the skyline, from the North Shore" },
    { id: "aq8s2lIbpCQ", lat: 49.2827, lon: -123.1207, label: "Skyline Panorama", place: "Vancouver", credit: "Vancouver Port Live", note: "Wide view of the city across the inlet; good at sunset" },
    { id: "GHEmhcWjiTE", lat: 49.2905, lon: -123.125, label: "Harbour Seaplanes", place: "Coal Harbour", credit: "Vancouver Port Live", note: "Seaplanes and ships against the skyline" },
    { id: "A-aYQjV2AJ4", lat: 49.2725, lon: -123.121, label: "False Creek", place: "Yaletown", credit: "Vancouver Yaletown Live", note: "Rotates between False Creek, the seawall and the skyline" },
    { id: "Mm_KgjdmWUc", lat: 49.2725, lon: -123.121, label: "False Creek Skyline", place: "Yaletown", credit: "Vancouver Yaletown Live", note: "Marinas and towers along False Creek" },
    { id: "aByyCUuaI0Q", lat: 49.27, lon: -123.115, label: "Cambie Bridge", place: "False Creek", credit: "Vancouver Yaletown Live", note: "Bridge traffic and boats" },
    { id: "gEVhrDQc8C0", lat: 49.2768, lon: -123.112, label: "BC Place", place: "Downtown Vancouver", credit: "Billyforce", note: "The stadium and Pacific Boulevard from above" },
    { id: "EGL7lTaxNcA", lat: 49.2827, lon: -123.1207, label: "Street View", place: "Vancouver", credit: "Vancouver Live Channel", note: "180° fisheye of a downtown street" },
  ]},
  { group: "Beaches & sunsets", icon: "Sunset", cams: [
    { id: "ClvJjIz5GE4", lat: 49.023, lon: -122.79, label: "White Rock East Beach", place: "Semiahmoo Bay", credit: "City of White Rock", note: "The bay, the pier and the mountains across the border" },
    { id: "JZ3UV60ykqc", lat: 49.086, lon: -125.875, label: "Long Beach", place: "Tofino", credit: "Long Beach Lodge", note: "Open Pacific surf beach" },
    { id: "Tbt6_787CB8", lat: 49.1, lon: -125.878, label: "Cox Bay", place: "Tofino", credit: "Pacific Sands", note: "Surf and sand, HD" },
    { id: "LqaP8m2OIqM", lat: 49.1, lon: -125.878, label: "Cox Bay Wide", place: "Tofino", credit: "Pacific Sands", note: "The whole bay from the resort" },
    { id: "4MK3E9EWDSY", lat: 49.0163, lon: -122.804, label: "White Rock Pier", place: "White Rock", credit: "City of White Rock", note: "The promenade and the long pier out into the bay" },
    { id: "6-cLoX0odoc", lat: 48.942, lon: -125.553, label: "Big Beach", place: "Ucluelet", credit: "UkeeTube", note: "Rocky shore through the trees, sunrise to sunset" },
    { id: "sLixr8cThJo", lat: 48.411, lon: -123.339, label: "Ross Bay", place: "Victoria", credit: "Ross Bay Live Webcam", note: "Looking out over the Strait of Juan de Fuca" },
  ]},
  { group: "Mountains & snow", icon: "MountainSnow", cams: [
    { id: "vLawo-FrBKk", lat: 49.366, lon: -122.949, label: "Mystery Peak", place: "Mt Seymour", credit: "Mt Seymour", note: "Alpine meadow and the peaks beyond, from the top of the ski area" },
    { id: "yQdKq9NuEa0", lat: 49.366, lon: -122.949, label: "Snow Stake", place: "Mt Seymour", credit: "Mt Seymour", note: "The Stoke-O-Meter snow board: how much fell overnight" },
  ]},
  { group: "Water & wildlife", icon: "Fish", cams: [
    { id: "9mg9PoFEX2U", lat: 49.3005, lon: -123.1309, label: "Sea Otters", place: "Vancouver Aquarium", credit: "Vancouver Aquarium", note: "The otter pool, from above" },
    { id: "_KXHUb0wFRE", lat: 49.3005, lon: -123.1309, label: "Sea Otters (cam 2)", place: "Vancouver Aquarium", credit: "Vancouver Aquarium", note: "The otters' rocky corner" },
    { id: "_qN4aao5kWc", lat: 49.153, lon: -125.907, label: "Tofino Harbour", place: "Tofino", credit: "Tofino Sea Kayaking", note: "Docks, boats and the mountains of Clayoquot Sound" },
    { id: "gUR8_ZvxdHA", lat: 48.783, lon: -123.045, label: "Saturna Island", place: "Gulf Islands", credit: "Randy Mennie", note: "Ships passing in Boundary Pass" },
    { id: "VF2WmFgrtGg", lat: 49.209, lon: -123.961, label: "Departure Bay", place: "Nanaimo", credit: "Departure Bay LIVE", note: "BC Ferries coming and going" },
    { id: "MaYMrtIXxvA", lat: 48.46, lon: -123.29, label: "Cadboro Bay", place: "Victoria", credit: "Royal Victoria Yacht Club", note: "Marina and bay, wide angle" },
  ]},
  { group: "Further afield", icon: "Globe", cams: [
    { id: "dfVK7ld38Ys", lat: 35.6595, lon: 139.7005, label: "Shibuya Crossing", place: "Tokyo, Japan", credit: "FNN", tz: "Asia/Tokyo", note: "The scramble crossing; busiest in Tokyo's evening (your early morning)" },
  ]},
].map((g) => ({ ...g, cams: g.cams.map((c) => ({ ...VAN, ...c, source: yt(c.id) })) }));

// a real current frame, grabbed by the hub; the channel's own thumbnail (often artwork) if that fails
export const snap = (src) => `/api/webcams/snapshot.jpg?type=${encodeURIComponent(src.type || "stream")}&url=${encodeURIComponent(src.url)}`;
export const thumb = (c) => `/api/webcams/thumb/${c.id}.jpg`;
