// Ready-made live webcams for the Webcams page: 24/7 YouTube live streams by city and kind, each checked live on
// 2026-10-20 (yt-dlp from the hub) and its picture looked at. Mt Seymour, White Rock Pier and Tofino Harbour need deno
// on the hub, see hub/webcams.py deno_path.
// A stream that restarts gets a new video id: if one stops working, Test on its page says so, and the channel
// usually has the new one under /streams. A channel with one camera that restarts every day or so (`live`) is
// played by its /live address, which follows the new stream.
const yt = (c) => ({ type: "youtube", url: c.live ? `https://www.youtube.com/${c.live}/live` : `https://www.youtube.com/watch?v=${c.id}` });

// kinds of webcam (the page's filter, the map's dot colours)
export const CATS = {
  skyline: { label: "Skylines & views", icon: "Building2", col: "#7c8cff" },
  street: { label: "Streets & squares", icon: "Footprints", col: "#ffb020" },
  water: { label: "Harbours & rivers", icon: "Anchor", col: "#2fb5ff" },
  beach: { label: "Beaches & surf", icon: "Sunset", col: "#ff7a59" },
  mountain: { label: "Mountains & sky", icon: "MountainSnow", col: "#d9e2ec" },
  wildlife: { label: "Animals & aquariums", icon: "Fish", col: "#34c38f" },
  transport: { label: "Planes, trains & bridges", icon: "Plane", col: "#c77dff" },
};

export const CITIES = [
  { key: "vancouver", label: "Vancouver & BC", tz: "America/Vancouver", cams: [
    { id: "DhkGXCgqWjg", lat: 49.2888, lon: -123.1111, label: "Vancouver Harbour", place: "Burrard Inlet & North Shore", cat: "skyline", credit: "Vancouver City Views", note: "Canada Place, the harbour and the North Shore mountains" },
    { id: "rxyNjFKwzJA", lat: 49.2827, lon: -123.1207, label: "Downtown Vancouver", place: "Skyline & viaducts", cat: "skyline", credit: "Vancouver City Views", note: "Downtown towers, Canada Place and the viaducts; the camera moves between views" },
    { id: "aq8s2lIbpCQ", lat: 49.3100, lon: -123.0800, label: "Skyline Panorama", place: "Vancouver", cat: "skyline", credit: "Vancouver Port Live", note: "Wide view of the city across the inlet; good at sunset" },
    { id: "GHEmhcWjiTE", lat: 49.2905, lon: -123.125, label: "Harbour Seaplanes", place: "Coal Harbour", cat: "transport", credit: "Vancouver Port Live", note: "Seaplanes and ships against the skyline" },
    { id: "A-aYQjV2AJ4", lat: 49.2725, lon: -123.121, label: "False Creek", place: "Yaletown", cat: "water", credit: "Vancouver Yaletown Live", note: "Rotates between False Creek, the seawall and the skyline" },
    { id: "Mm_KgjdmWUc", lat: 49.2730, lon: -123.1195, label: "False Creek Skyline", place: "Yaletown", cat: "skyline", credit: "Vancouver Yaletown Live", note: "Marinas and towers along False Creek" },
    { id: "za6UZWBd6qY", lat: 49.2700, lon: -123.1040, label: "False Creek East", place: "Vancouver", cat: "water", credit: "Chris Boenigk", live: "@chrisboenigk3359", note: "The east end of False Creek, Science World and the towers" },
    { id: "aByyCUuaI0Q", lat: 49.27, lon: -123.115, label: "Cambie Bridge", place: "False Creek", cat: "transport", credit: "Vancouver Yaletown Live", note: "Bridge traffic and boats" },
    { id: "gEVhrDQc8C0", lat: 49.2768, lon: -123.112, label: "BC Place", place: "Downtown Vancouver", cat: "skyline", credit: "Billyforce Entertainment", note: "The stadium and Pacific Boulevard from above" },
    { id: "EGL7lTaxNcA", lat: 49.2820, lon: -123.1190, label: "Street View", place: "Vancouver", cat: "street", credit: "Vancouver Live Channel", note: "180° fisheye of a downtown street" },
    { id: "T0oUufecXeE", lat: 49.3261, lon: -122.9482, label: "Deep Cove", place: "North Vancouver", cat: "water", credit: "Paddler's Paradise", note: "The marina and Indian Arm with the mountains behind" },
    { id: "ClvJjIz5GE4", lat: 49.023, lon: -122.79, label: "White Rock East Beach", place: "Semiahmoo Bay", cat: "beach", credit: "City of White Rock", note: "The bay, the pier and the mountains across the border" },
    { id: "4MK3E9EWDSY", lat: 49.0163, lon: -122.804, label: "White Rock Pier", place: "White Rock", cat: "beach", credit: "City of White Rock", note: "The promenade and the long pier out into the bay" },
    { id: "vLawo-FrBKk", lat: 49.366, lon: -122.949, label: "Mystery Peak", place: "Mt Seymour", cat: "mountain", credit: "Mt Seymour", note: "Alpine meadow and the peaks beyond, from the top of the ski area" },
    { id: "yQdKq9NuEa0", lat: 49.3665, lon: -122.9495, label: "Snow Stake", place: "Mt Seymour", cat: "mountain", credit: "Mt Seymour", note: "The Stoke-O-Meter snow board: how much fell overnight" },
    { id: "OSKgIDnBwwQ", lat: 49.3959, lon: -123.2062, label: "Cypress Mountain", place: "West Vancouver", cat: "mountain", credit: "Cypress Mountain", note: "The Olympic plaza at the base of the ski runs" },
    { id: "IEhDUXECe_k", lat: 50.1195, lon: -122.9579, label: "Whistler Village", place: "Whistler", cat: "mountain", credit: "GoWhistler", note: "The village plaza with the peaks above" },
    { id: "9mg9PoFEX2U", lat: 49.3005, lon: -123.1309, label: "Sea Otters", place: "Vancouver Aquarium", cat: "wildlife", credit: "Vancouver Aquarium", note: "The otter pool, from above" },
    { id: "_KXHUb0wFRE", lat: 49.3007, lon: -123.1307, label: "Sea Otters (cam 2)", place: "Vancouver Aquarium", cat: "wildlife", credit: "Vancouver Aquarium", note: "The otters' rocky corner" },
    { id: "JZ3UV60ykqc", lat: 49.086, lon: -125.875, label: "Long Beach", place: "Tofino", cat: "beach", credit: "Long Beach Lodge Resort", note: "Open Pacific surf beach" },
    { id: "Tbt6_787CB8", lat: 49.1, lon: -125.878, label: "Cox Bay", place: "Tofino", cat: "beach", credit: "Pacific Sands Beach Resort", note: "Surf and sand, HD" },
    { id: "LqaP8m2OIqM", lat: 49.1005, lon: -125.8775, label: "Cox Bay Wide", place: "Tofino", cat: "beach", credit: "Pacific Sands Beach Resort", note: "The whole bay from the resort" },
    { id: "6-cLoX0odoc", lat: 48.942, lon: -125.553, label: "Big Beach", place: "Ucluelet", cat: "beach", credit: "UkeeTube", note: "Rocky shore through the trees, sunrise to sunset" },
    { id: "_qN4aao5kWc", lat: 49.153, lon: -125.907, label: "Tofino Harbour", place: "Tofino", cat: "water", credit: "Tofino Sea Kayaking (Paddle TSK)", note: "Docks, boats and the mountains of Clayoquot Sound" },
    { id: "sLixr8cThJo", lat: 48.411, lon: -123.339, label: "Ross Bay", place: "Victoria", cat: "beach", credit: "Ross Bay Live Webcam", note: "Looking out over the Strait of Juan de Fuca" },
    { id: "MaYMrtIXxvA", lat: 48.46, lon: -123.29, label: "Cadboro Bay", place: "Victoria", cat: "water", credit: "Royal Victoria Yacht Club", note: "Marina and bay, wide angle" },
    { id: "gUR8_ZvxdHA", lat: 48.783, lon: -123.045, label: "Saturna Island", place: "Gulf Islands", cat: "transport", credit: "Randy Mennie", note: "Ships passing in Boundary Pass" },
    { id: "VF2WmFgrtGg", lat: 49.209, lon: -123.961, label: "Departure Bay", place: "Nanaimo", cat: "transport", credit: "Departure Bay LIVE", note: "BC Ferries coming and going" },
  ]},
  { key: "calgary", label: "Calgary & Rockies", tz: "America/Edmonton", cams: [
    { id: "MwcqP3ta6RI", lat: 51.0563, lon: -114.0650, label: "Calgary from McHugh Bluff", place: "Calgary", cat: "skyline", credit: "DELTATECH Calgary", note: "Downtown towers and the Bow River, the Rockies on clear days" },
    { id: "xsRDTfuksyI", lat: 51.0408, lon: -114.0727, label: "Central Memorial Park", place: "Calgary", cat: "street", credit: "DELTATECH Calgary", note: "The Beltline park with downtown behind" },
    { id: "oe68sJ1ZtsA", lat: 51.0849, lon: -115.3513, label: "Canmore Spring Creek", place: "Canmore", cat: "mountain", credit: "Spring Creek", note: "The Three Sisters and the Bow Valley" },
    { id: "_0wPODlF9wU", lat: 51.0891, lon: -115.3633, label: "Canmore Main Street", place: "Canmore", cat: "street", credit: "CanmoreAlberta.com", note: "Main Street with the Rockies at the end of it" },
  ]},
  { key: "edmonton", label: "Edmonton", tz: "America/Edmonton", cams: [
    { id: "1QMmvBAZlEw", lat: 53.5444, lon: -113.4909, label: "Downtown Edmonton", place: "Edmonton", cat: "skyline", credit: "YEG er", live: "@YEGer_VIEW", note: "Wide view across downtown from a tower" },
  ]},
  { key: "toronto", label: "Ontario & Québec", tz: "America/Toronto", cams: [
    { id: "LxluAbiabrQ", lat: 43.6370, lon: -79.4000, label: "Toronto Skyline", place: "Gardiner Expressway", cat: "skyline", credit: "Toronto Live View", live: "@TorontoLiveView", note: "Downtown towers from a condo above the Gardiner" },
    { id: "qx7gry390YA", lat: 43.0800, lon: -79.0750, label: "Niagara Falls", place: "Niagara Falls", cat: "water", credit: "EarthCam", note: "The Horseshoe Falls and the mist" },
    { id: "yF7XdpkL34w", lat: 42.8870, lon: -79.2500, label: "Welland Canal", place: "Port Colborne", cat: "transport", credit: "StreamTime Live", note: "Lake freighters entering the canal from Lake Erie" },
    { id: "g9hNGJxw6Yw", lat: 42.8980, lon: -78.9144, label: "Peace Bridge", place: "Fort Erie", cat: "transport", credit: "Peace Bridge Authority", note: "Trucks at the Canada-bound inspection plaza" },
    { id: "63HFDttwmdc", lat: 45.6300, lon: -72.9600, label: "Québec Night Sky", place: "Saint-Hyacinthe", cat: "mountain", credit: "AuroraQC", live: "@AuroraQc_officiel", note: "An all-sky camera for northern lights, with the weather" },
    { id: "DnUFAShZKus", lat: 42.9066, lon: -78.9049, label: "Peace Bridge River", place: "Buffalo", cat: "transport", credit: "Peace Bridge Authority", note: "Canada-bound traffic on the bridge over the Niagara River" },
  ]},
  { key: "seattle", label: "Seattle", tz: "America/Los_Angeles", cams: [
    { id: "9-hGCRgqMMs", lat: 47.6606, lon: -122.3146, label: "UW Continuum College", place: "Seattle", cat: "skyline", credit: "University of Washington", note: "Union Bay and the 520 bridge over the campus" },
    { id: "NCzKkn0wD30", lat: 47.6553, lon: -122.3035, label: "Lake Washington", place: "Seattle", cat: "water", credit: "University of Washington", note: "Husky Stadium and Lake Washington" },
    { id: "xbx61YpBlgc", lat: 47.6560, lon: -122.3095, label: "UW Red Square", place: "Seattle", cat: "street", credit: "University of Washington", note: "The brick plaza at the heart of campus" },
    { id: "e4IbEGi1WAI", lat: 47.6574, lon: -122.3076, label: "UW Quad", place: "Seattle", cat: "street", credit: "University of Washington", note: "The Quad's cherry trees, best in spring" },
  ]},
  { key: "sf", label: "San Francisco & Monterey", tz: "America/Los_Angeles", cams: [
    { id: "BSWhGNXxT9A", lat: 37.8235, lon: -122.3710, label: "Treasure Island", place: "San Francisco Bay", cat: "skyline", credit: "Teleport.camera", note: "The city skyline and the Golden Gate across the bay" },
    { id: "PDKGik7Np88", lat: 37.8037, lon: -122.4012, label: "Pier 23", place: "San Francisco", cat: "water", credit: "StreamTime Live", note: "Ships and ferries on the bay from the Embarcadero" },
    { id: "c4SUwuF2wfo", lat: 37.7609, lon: -122.4350, label: "Castro Street", place: "San Francisco", cat: "street", credit: "Castro Street Cam 1", live: "@castrostreetcam1693", note: "The Castro's main street and its crossing" },
    { id: "lJve6cr_D_U", lat: 37.7612, lon: -122.4348, label: "Castro Street (cam 2)", place: "San Francisco", cat: "street", credit: "Castro Street Cam 2", live: "@castrostreetcam2549", note: "Another angle on Castro Street" },
    { id: "hlXyNgnmH8M", lat: 37.8800, lon: -122.3050, label: "I-80 Berkeley", place: "Berkeley", cat: "transport", credit: "I-80 Bay Area Live Traffic", live: "@I-80BayAreaLiveTraffic", note: "Freeway traffic along the bay" },
    { id: "iQ7XJDXYSMo", lat: 37.6178, lon: -122.4930, label: "Mori Point", place: "Pacifica", cat: "beach", credit: "Chamberlin Nature", note: "Headland and surf south of the city, 4K" },
    { id: "_-_hg_aJrKU", lat: 37.6325, lon: -122.4962, label: "Pacifica Pier", place: "Pacifica", cat: "beach", credit: "Chamberlin Nature", note: "The pier and beach, 4K" },
    { id: "BrUaOjjXgZM", lat: 37.6330, lon: -122.4965, label: "Pacifica Pier Close-up", place: "Pacifica", cat: "beach", credit: "Chamberlin Nature", note: "Anglers and waves on the pier, 4K" },
    { id: "lmec_rcEIL4", lat: 37.6280, lon: -122.4950, label: "Sharp Park Beach", place: "Pacifica", cat: "beach", credit: "Chamberlin Nature", note: "Surf rolling in, 4K" },
    { id: "bV4EiHr53ek", lat: 37.7699, lon: -122.4661, label: "African Penguins", place: "California Academy of Sciences", cat: "wildlife", credit: "California Academy of Sciences", note: "The penguin colony" },
    { id: "eFak1bHw6Us", lat: 37.7700, lon: -122.4660, label: "Penguins Underwater", place: "California Academy of Sciences", cat: "wildlife", credit: "California Academy of Sciences", note: "The penguins swimming, from below" },
    { id: "99aULx3GORo", lat: 37.7698, lon: -122.4662, label: "Coral Reef", place: "California Academy of Sciences", cat: "wildlife", credit: "California Academy of Sciences", note: "The Philippine coral reef tank" },
    { id: "fHb0eB9RUgA", lat: 37.6989, lon: -123.0031, label: "Farallon Islands", place: "Gulf of the Farallones", cat: "wildlife", credit: "California Academy of Sciences", note: "Seabirds and sea lions 30 miles offshore" },
    { id: "abbR-Ttd-cA", lat: 36.6182, lon: -121.9015, label: "Monterey Sea Otters", place: "Monterey Bay Aquarium", cat: "wildlife", credit: "Monterey Bay Aquarium", note: "The rescued otters' exhibit" },
    { id: "w3LjpFhySTg", lat: 36.6184, lon: -121.9017, label: "Kelp Forest", place: "Monterey Bay Aquarium", cat: "wildlife", credit: "Monterey Bay Aquarium", note: "Fish gliding through a three-storey kelp tank" },
    { id: "zL68biE6wAs", lat: 36.6180, lon: -121.9013, label: "Moon Jellies", place: "Monterey Bay Aquarium", cat: "wildlife", credit: "Monterey Bay Aquarium", note: "Glowing jellies; hypnotic on the wall" },
    { id: "n_GpVsz4nHU", lat: 36.6186, lon: -121.9019, label: "Open Sea", place: "Monterey Bay Aquarium", cat: "wildlife", credit: "Monterey Bay Aquarium", note: "Tuna, sharks and sardine schools" },
    { id: "tEtg5Kg3voQ", lat: 36.6181, lon: -121.9020, label: "Shark Cam", place: "Monterey Bay Aquarium", cat: "wildlife", credit: "Monterey Bay Aquarium", note: "Sharks in the Monterey Bay habitats tank" },
    { id: "fVa6-zCBR7A", lat: 36.6190, lon: -121.9010, label: "Monterey Bay", place: "Monterey", cat: "beach", credit: "Monterey Bay Aquarium", note: "The bay from the aquarium's deck" },
  ]},
  { key: "la", label: "LA & Orange County", tz: "America/Los_Angeles", cams: [
    { id: "mhQjsLBfOoY", lat: 33.6566, lon: -118.0048, label: "Huntington Pier", place: "Huntington Beach", cat: "beach", credit: "City of Huntington Beach", note: "Surfers by the pier" },
    { id: "RGYlFjV-dtc", lat: 33.6527, lon: -118.0057, label: "Huntington Lifeguard Tower", place: "Huntington Beach", cat: "beach", credit: "City of Huntington Beach", note: "The beach from a lifeguard station" },
  ]},
  { key: "austin", label: "Austin", tz: "America/Chicago", cams: [
    { id: "_GELzaDdo40", lat: 30.3900, lon: -97.9100, label: "Lake Travis", place: "Austin", cat: "water", credit: "Lake Travis Lake Cam", live: "@LTLakeCam", note: "Hill Country lake west of Austin, with the weather" },
  ]},
  { key: "boston", label: "Boston & Massachusetts", tz: "America/New_York", cams: [
    { id: "wPCrbAjyl6U", lat: 42.3554, lon: -71.0640, label: "Boston Common", place: "Boston", cat: "skyline", credit: "Third Polar Arch", note: "The Public Garden and the Back Bay skyline" },
    { id: "b_zG3YKI3H8", lat: 42.3500, lon: -71.0500, label: "Boston Sky", place: "Boston", cat: "skyline", credit: "Moanilani with a smile", live: "@moanilani", note: "The skyline and the sky over Boston, 4K" },
    { id: "v3cuH_7swWQ", lat: 42.2689, lon: -71.6139, label: "Westborough Rotary", place: "Westborough", cat: "street", credit: "Westborough TV", note: "The rotary in a central Massachusetts town (the nearest live cam to Fitchburg)" },
    { id: "_OeFhA2XqqA", lat: 41.7394, lon: -70.6256, label: "Cape Cod Canal", place: "Bourne", cat: "transport", credit: "Massachusetts Maritime Academy", note: "Ships and tugs through the canal" },
    { id: "TE6N2eDrsR0", lat: 41.5233, lon: -70.6715, label: "Woods Hole Ferry", place: "Woods Hole", cat: "transport", credit: "Go Martha's Vineyard", note: "Island ferries at the dock" },
    { id: "moLxu7LGuKk", lat: 41.5258, lon: -70.6741, label: "Eel Pond", place: "Woods Hole", cat: "water", credit: "Marine Biological Laboratory", note: "The little harbour and its drawbridge" },
    { id: "REpNAzupM9Y", lat: 41.6644, lon: -70.0749, label: "Harwich Beach", place: "Cape Cod", cat: "beach", credit: "Winstead Inn", note: "Nantucket Sound from a beachfront inn" },
    { id: "Y0BRntF3178", lat: 41.4541, lon: -70.6030, label: "Vineyard Haven", place: "Martha's Vineyard", cat: "water", credit: "Go Martha's Vineyard", note: "The harbour, moorings and ferry dock from above" },
    { id: "MAuXQgZsq3Y", lat: 41.3540, lon: -70.7670, label: "Menemsha Harbor", place: "Martha's Vineyard", cat: "water", credit: "Go Martha's Vineyard", note: "The fishing village's docks" },
    { id: "a_WjWhIsVVU", lat: 41.4224, lon: -70.6104, label: "Vineyard Alpacas", place: "Martha's Vineyard", cat: "wildlife", credit: "Go Martha's Vineyard", note: "Alpacas grazing in their pasture" },
  ]},
  { key: "nyc", label: "New York", tz: "America/New_York", cams: [
    { id: "JQ_jwk_7OVE", lat: 40.7580, lon: -73.9855, label: "Times Square", place: "New York", cat: "street", credit: "EarthCam", note: "Looking north up Times Square, 4K" },
    { id: "o5Ie_LpZ9uk", lat: 40.6892, lon: -74.0445, label: "Statue of Liberty", place: "New York Harbor", cat: "skyline", credit: "EarthCam", note: "Lady Liberty up close" },
    { id: "5C9oM7C2Q9k", lat: 40.7127, lon: -74.0134, label: "World Trade Center", place: "Lower Manhattan", cat: "skyline", credit: "EarthCam", note: "One World Trade and the downtown towers" },
    { id: "ZYQlLQHz2Mg", lat: 40.7115, lon: -74.0132, label: "9/11 Memorial", place: "Lower Manhattan", cat: "street", credit: "EarthCam", note: "The memorial plaza and pools" },
    { id: "2_PDaUJbfuI", lat: 40.7529, lon: -73.9787, label: "SUMMIT One Vanderbilt", place: "Midtown Manhattan", cat: "skyline", credit: "EarthCam", note: "Midtown from the top of One Vanderbilt" },
    { id: "zKDNVIGoOSQ", lat: 40.7590, lon: -74.0022, label: "Javits Center Roof", place: "Hell's Kitchen", cat: "skyline", credit: "Javits Center", note: "Midtown and the Hudson Yards towers" },
    { id: "sixP_dAJ8D4", lat: 40.7598, lon: -74.0237, label: "Lincoln Harbor", place: "Weehawken", cat: "skyline", credit: "EarthCam", note: "Manhattan's skyline across the Hudson" },
    { id: "l7b5RFAkKhY", lat: 40.6900, lon: -74.0100, label: "New York Harbor", place: "Brooklyn", cat: "water", credit: "StreamTime Live", note: "Ships, ferries and the Statue of Liberty from Brooklyn" },
    { id: "H67j7H-7QD0", lat: 40.5749, lon: -73.9859, label: "Coney Island", place: "Brooklyn", cat: "beach", credit: "EarthCam", note: "The boardwalk, beach and Wonder Wheel" },
    { id: "bM438ptlpvE", lat: 40.6602, lon: -73.9690, label: "Prospect Park", place: "Brooklyn", cat: "street", credit: "Prospect Park Live", live: "@ProspectParkLive", note: "A leafy Park Slope street beside the park" },
    { id: "5BlEY0fpCRs", lat: 40.8517, lon: -73.9527, label: "George Washington Bridge", place: "New York", cat: "transport", credit: "GWB Live Traffic Cam", live: "@gwblivetrafficcam", note: "Traffic on the bridge from the Manhattan side" },
  ]},
  { key: "dc", label: "Washington", tz: "America/New_York", cams: [
    { id: "oDCAAfOSqvA", lat: 38.8895, lon: -77.0353, label: "Washington Monument", place: "Washington, D.C.", cat: "skyline", credit: "EarthCam", note: "The monument over the National Mall" },
  ]},
  { key: "us", label: "More US cities", tz: "America/Chicago", cams: [
    { id: "O0UGT7AT3aw", lat: 41.8789, lon: -87.6359, label: "Skydeck Chicago", place: "Willis Tower", cat: "skyline", credit: "EarthCam", note: "The Loop from the 103rd floor" },
    { id: "Ecl2A5JYdtA", lat: 41.8500, lon: -87.6200, label: "Chicago Southern Skyline", place: "Chicago", cat: "skyline", credit: "Chicago Southern Skyline", live: "@ChicagoSouthSky", note: "Willis Tower and the Loop over the rooftops" },
    { id: "EiUyBIcG4fQ", lat: 41.7868, lon: -87.7522, label: "Midway Airport", place: "Chicago", cat: "transport", credit: "EarthCam", note: "Landings at Midway, close to the houses" },
    { id: "KJuGU7v5oYw", lat: 36.1126, lon: -115.1767, label: "Bellagio Conservatory", place: "Las Vegas", cat: "street", credit: "EarthCam", tz: "America/Los_Angeles", note: "The hotel's seasonal flower displays" },
    { id: "RqOhOunra9Y", lat: 20.7500, lon: -156.4500, label: "Maui Whale Watch", place: "Maui", cat: "beach", credit: "Maui Live Cam", tz: "Pacific/Honolulu", live: "@mauilivecam7143", note: "Beach and ocean in west Maui; humpbacks in winter, 4K" },
    { id: "xqdukYhcGEU", lat: 29.9584, lon: -90.0651, label: "Bourbon Street", place: "New Orleans", cat: "street", credit: "EarthCam", note: "The French Quarter's party street" },
    { id: "QxFFjXu_zqs", lat: 29.9590, lon: -90.0645, label: "French Quarter Balcony", place: "New Orleans", cat: "street", credit: "EarthCam", note: "Bourbon Street from a balcony above" },
    { id: "hPvl6IACa2k", lat: 20.7182, lon: -156.4495, label: "Kihei Surf", place: "Maui", cat: "beach", credit: "The Hale Pau Hana", tz: "Pacific/Honolulu", note: "Waves on a south Maui beach" },
  ]},
  { key: "world", label: "Further afield", tz: "UTC", cams: [
    { id: "dfVK7ld38Ys", lat: 35.6595, lon: 139.7005, label: "Shibuya Crossing", place: "Tokyo, Japan", cat: "street", credit: "FNN", tz: "Asia/Tokyo", note: "The scramble crossing; busiest in Tokyo's evening (your early morning)" },
    { id: "IBg0YLhlbCE", lat: 35.7000, lon: 139.7740, label: "Akihabara", place: "Tokyo, Japan", cat: "street", credit: "Akihabara Live Cam", tz: "Asia/Tokyo", live: "@Livecamakiba", note: "Trains and streets north of Akihabara station" },
    { id: "Ac0E6CrnDxA", lat: 35.6270, lon: 139.7750, label: "Tokyo Bay", place: "Odaiba, Japan", cat: "skyline", credit: "Odaiba Live Camera", tz: "Asia/Tokyo", note: "The Rainbow Bridge and Tokyo's skyline, 4K" },
    { id: "U2UfQH4lHAY", lat: -33.8460, lon: 151.2100, label: "Sydney Harbour", place: "Sydney, Australia", cat: "skyline", credit: "Sydney Harbour LIVE", tz: "Australia/Sydney", live: "@SydneyHarbourLIVE", note: "The Harbour Bridge and Opera House" },
    { id: "zMCea32gpmg", lat: 51.5320, lon: -0.1779, label: "Abbey Road", place: "London, England", cat: "street", credit: "EarthCam", tz: "Europe/London", note: "Tourists recreating the Beatles' crossing" },
    { id: "hji70TmnT7M", lat: 51.5090, lon: -0.1960, label: "Notting Hill Gate", place: "London, England", cat: "street", credit: "Notting Hill Gate Cam", tz: "Europe/London", live: "@NottingHillGateCam", note: "Buses and shoppers outside the station, 4K" },
    { id: "3nyPER2kzqk", lat: 53.3450, lon: -6.2640, label: "Dublin", place: "Dublin, Ireland", cat: "street", credit: "EarthCam", tz: "Europe/Dublin", note: "Temple Bar's corner" },
  ]},
];

// every webcam, flat: its city and time zone filled in, and its source
export const GALLERY = CITIES.flatMap((x) => x.cams.map((c) => ({ tz: x.tz, ...c, city: x.key, cityLabel: x.label, source: yt(c) })));

// a real current frame, grabbed by the hub; the channel's own thumbnail (often artwork) if that fails
export const snap = (src) => `/api/webcams/snapshot.jpg?type=${encodeURIComponent(src.type || "stream")}&url=${encodeURIComponent(src.url)}`;
export const thumb = (c) => `/api/webcams/thumb/${c.id}.jpg`;
