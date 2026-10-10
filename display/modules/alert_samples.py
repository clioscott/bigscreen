"""Sample alerts, one per hazard group, for previewing the alert screen (control panel + "test on the wall").

Written in ECCC's style (summary, timing, impacts, advice, boilerplate) with Metro Vancouver
zone names, so they exercise the same parsing and layout as real alerts. Some cover the home
city (Richmond), some are nearby-only, to show both looks. `map_cities` are the municipalities
outlined on the sample's map.
"""

MV = "Metro Vancouver - "
CENTRAL = MV + "central including the City of Vancouver, Burnaby and New Westminster"
NSHORE = MV + "North Shore including West Vancouver and North Vancouver"
NE = MV + "northeast including Coquitlam, Port Coquitlam, Port Moody, Maple Ridge and Pitt Meadows"
SE = MV + "southeast including Surrey, White Rock and Langley"
SW = MV + "southwest including Richmond and Delta"
BOILER = "\n\nPlease continue to monitor alerts and forecasts issued by Environment Canada."

SAMPLES = {
    "rain": {"type": "warning", "name": "rainfall warning", "regions": [CENTRAL, NSHORE, NE, SE, SW],
             "map_cities": ["Vancouver", "Burnaby", "New Westminster", "West Vancouver", "North Vancouver", "Coquitlam",
                            "Port Coquitlam", "Port Moody", "Maple Ridge", "Pitt Meadows", "Surrey", "White Rock",
                            "Langley", "Richmond", "Delta"],
             "text": "Heavy rain is expected, with total rainfall amounts of 50 to 70 mm, and up to 100 mm near the North "
                     "Shore mountains.\n\nAn atmospheric river will bring heavy rain to the region beginning tonight and "
                     "continuing through Saturday. Rainfall rates of 5 to 10 mm per hour are possible at times overnight.\n\n"
                     "Water will likely pool on roads and in low-lying areas. Localized flooding and washouts near rivers, "
                     "creeks and culverts are possible.\n\nDon't drive through flooded roadways. Allow extra time for travel."
                     + BOILER},
    "snow": {"type": "warning", "name": "snowfall warning", "regions": [CENTRAL, SW, SE],
             "map_cities": ["Vancouver", "Burnaby", "New Westminster", "Richmond", "Delta", "Surrey", "White Rock", "Langley"],
             "text": "Snowfall with total amounts of 10 to 15 cm is expected beginning this evening and continuing through "
                     "Saturday morning.\n\nA low pressure system will spread snow across the region tonight. Snowfall rates "
                     "of 2 to 3 cm per hour are possible near midnight. Snow will change to rain Saturday afternoon.\n\n"
                     "Travel will likely be challenging. Visibility will likely be reduced at times.\n\n"
                     "Allow extra time for travel. Prepare for quickly changing and deteriorating road conditions." + BOILER},
    "freezing": {"type": "warning", "name": "freezing rain warning", "regions": [NE, SE],
                 "map_cities": ["Coquitlam", "Port Coquitlam", "Port Moody", "Maple Ridge", "Pitt Meadows", "Surrey", "Langley"],
                 "text": "Periods of freezing rain are expected this morning.\n\nCold air trapped in the Fraser Valley will "
                         "cause rain to freeze on contact with the ground until temperatures rise above zero early this "
                         "afternoon.\n\nSurfaces such as highways, roads, walkways and parking lots may become icy and "
                         "slippery. Local utility outages are possible.\n\nTake extra care when walking or driving in "
                         "affected areas." + BOILER},
    "wind": {"type": "warning", "name": "wind warning", "regions": [SW, CENTRAL, NSHORE],
             "map_cities": ["Richmond", "Delta", "Vancouver", "Burnaby", "New Westminster", "West Vancouver", "North Vancouver"],
             "text": "Strong winds that may cause damage are expected.\n\nSoutheast winds of 70 km/h gusting to 90 km/h will "
                     "develop near the water this evening ahead of an intense Pacific frontal system. Winds will ease "
                     "overnight.\n\nLocal utility outages are possible. Damage to roofs, fences and trees is possible.\n\n"
                     "Secure loose objects such as patio furniture. If outside, protect yourself from flying debris." + BOILER},
    "thunder": {"type": "watch", "name": "severe thunderstorm watch", "regions": [NE, SE],
                "map_cities": ["Coquitlam", "Port Coquitlam", "Port Moody", "Maple Ridge", "Pitt Meadows", "Surrey", "Langley"],
                "text": "Conditions are favourable for the development of severe thunderstorms that may be capable of "
                        "producing strong wind gusts, large hail and heavy rain.\n\nThunderstorms are expected to develop "
                        "over the eastern Fraser Valley this afternoon and drift westward this evening.\n\nLarge hail can "
                        "damage property and cause injury. Lightning kills and injures people every year.\n\nWhen thunder "
                        "roars, go indoors." + BOILER},
    "tornado": {"type": "warning", "name": "tornado warning", "regions": [SE],
                "map_cities": ["Surrey", "White Rock", "Langley"],
                "text": "A tornado is possible. Meteorologists are tracking a severe thunderstorm that is capable of "
                        "producing a tornado. This storm is located near Langley, moving east at 30 km/h.\n\nDamage to "
                        "buildings, such as to roofs and windows, may occur.\n\nGo indoors to a room on the lowest floor such "
                        "as a basement, away from windows. If outdoors and you can't reach a safe place, lie down in a ditch."
                        + BOILER},
    "flood": {"type": "warning", "name": "coastal flooding warning", "regions": [SW, CENTRAL],
              "map_cities": ["Richmond", "Delta", "Vancouver"],
              "text": "High water levels combined with strong winds and waves may cause coastal flooding.\n\nA storm surge of "
                      "40 to 60 cm will coincide with the morning high tide near 8:00 a.m. Saturday.\n\nLow-lying coastal "
                      "areas, including dikes, seawalls and beaches, may flood. Waves may wash over seawalls.\n\nStay away "
                      "from the shoreline and dikes during high tide." + BOILER},
    "heat": {"type": "warning", "name": "heat warning", "regions": [CENTRAL, SW, SE, NE, NSHORE],
             "map_cities": ["Vancouver", "Burnaby", "Richmond", "Delta", "Surrey", "Langley", "Coquitlam", "North Vancouver"],
             "text": "A period of very hot temperatures is expected.\n\nDaytime highs of 29 to 33 degrees with overnight "
                     "lows near 17 degrees are expected Saturday through Monday. Inland areas will be hotter.\n\nHigh "
                     "temperatures increase the risk of heat illnesses for everyone.\n\nDrink plenty of water. Check on "
                     "older family, friends and neighbours. Never leave people or pets inside a parked vehicle." + BOILER},
    "cold": {"type": "warning", "name": "arctic outflow warning", "regions": [NSHORE, "Howe Sound"],
             "map_cities": ["West Vancouver", "North Vancouver", "Lions Bay", "Bowen Island Municipality"],
             "text": "Strong arctic outflow winds will give wind chill values near minus 20.\n\nOutflow winds of 50 km/h "
                     "gusting to 70 km/h will develop tonight through Howe Sound and the North Shore and continue through "
                     "Tuesday.\n\nFrostbite and hypothermia can occur if precautions are not taken.\n\nDress warmly in "
                     "layers and cover up exposed skin." + BOILER},
    "frost": {"type": "advisory", "name": "frost advisory", "regions": [NE, SE],
              "map_cities": ["Maple Ridge", "Pitt Meadows", "Coquitlam", "Langley", "Surrey"],
              "text": "Temperatures will drop near the freezing mark overnight tonight.\n\nClear skies and light winds will "
                      "allow temperatures to fall to near 0 degrees by early morning in the eastern suburbs.\n\nTake "
                      "preventative measures to protect cold-sensitive plants, trees, and crops." + BOILER},
    "fog": {"type": "advisory", "name": "fog advisory", "regions": [SW, CENTRAL],
            "map_cities": ["Richmond", "Delta", "Vancouver", "Burnaby"],
            "text": "Near-zero visibility in fog is expected or occurring.\n\nDense fog has developed near the Fraser River "
                    "and over Vancouver International Airport this morning and will lift by noon.\n\nTravel may be "
                    "hazardous due to near-zero visibility.\n\nReduce your speed and keep a safe distance from the vehicle "
                    "ahead. Use your low beams." + BOILER},
    "smoke": {"type": "statement", "name": "special air quality statement", "regions": [CENTRAL, SW, SE, NE, NSHORE],
              "map_cities": ["Vancouver", "Burnaby", "Richmond", "Delta", "Surrey", "Langley", "Coquitlam", "North Vancouver"],
              "text": "Wildfire smoke is causing poor air quality and reducing visibility.\n\nSmoke from wildfires in the "
                      "interior will continue to affect the region today and Sunday. Air quality may improve Monday as "
                      "winds turn onshore.\n\nPeople more likely to be impacted include seniors, children and people with "
                      "existing illnesses.\n\nLimit time outdoors. Keep windows closed and use an air filter." + BOILER},
    "general": {"type": "statement", "name": "special weather statement", "regions": ["Fraser Valley - west including Abbotsford", "Howe Sound"],
                "map_cities": ["Abbotsford", "Mission", "Lions Bay", "Bowen Island Municipality"],
                "text": "A significant change in the weather is expected this weekend.\n\nA strong Pacific system will bring "
                        "heavy rain, strong winds and much cooler temperatures beginning Saturday night. Snow levels will "
                        "fall to 800 metres.\n\nTravel over mountain passes may become challenging.\n\nWatch for updated "
                        "forecasts and any potential weather alerts as the event nears." + BOILER},
}
