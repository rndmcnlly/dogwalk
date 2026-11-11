// Handles (DOMAIN.md): a User's spoken, nonsecret identifier, two words assigned at
// Registration. Words come from EFF short wordlist 2 (CC BY 3.0 US, eff.org/dice), whose
// unique three-letter prefixes tolerate speech-recognition errors, minus every PGP word the
// Invite Codes use (invitations.ts), so a transcript tells the two apart by vocabulary as well
// as by length, minus grim, identity, brand, and bodily words, and minus function words and
// the Identity Check's escape words, which ordinary speech would collide with.
export const HANDLE_WORDS = (
  "abbreviate abiding absorbing abundant academy accountant acoustics acquire acrobat actress acuteness aerosol " +
  "aesthetic afloat aftershave agency agreeing ajar alarmclock albatross alchemy alfalfa algae alkaline almanac " +
  "alphabet altitude aluminum amazingly amendment amiable amoeba amplifier amuser anagram anchor android " +
  "angelfish animal anklet announcer antelope apartment apostrophe apricot aquamarine arachnid arbitrate " +
  "ardently arena argument aristocrat armchair aromatic arrowhead artichoke ascend asleep asparagus astronaut " +
  "asymmetric atom attic auctioneer auditorium augmented auspicious automobile auxiliary avalanche avenue " +
  "aviator avocado awareness awning awoke axially azalea babbling backpack bagpipe bakery balancing bamboo " +
  "banana barracuda basket bathrobe blender blimp blouse boatyard bobcat boiler bonnet boots borough bottle " +
  "bouquet boxlike broom brushes bubblegum buckle buffalo bullfrog bunny busboy cabin cactus cafeteria cakewalk " +
  "calculator camera canister capsule carrot cashew cathedral caviar cedar celery ceramics chalkboard " +
  "cheesecake chimney chlorine chopsticks chrome cilantro cinnamon circle cityscape civilian clay clipboard " +
  "clock clubhouse coathanger coconut codeword coexistent coffeecake computer confetti copier cosmetics cotton " +
  "couch coverless coyote coziness crawfish crewmember croissant crumble crystal cubical cucumber cuddly " +
  "cufflink cuisine cup curry cushion cybernetic cyclist cylinder cymbal cypress dachshund daffodil dairy " +
  "dalmatian dandelion dartboard datebook dawn daytime dazzler dealer decal dedicate deepness defrost degree " +
  "dehydrator deliverer depot desktop device dexterity diamond dictionary diffuser digit dimple dinnerware " +
  "diploma directory dishcloth dividers dominoes donut doorstep dorsal double downstairs dozed drainpipe " +
  "dresser driftwood drum dryer duckling duffel dugout duplex durable dustpan dutiful duvet eagerness earlobe " +
  "easel ebook eccentric echoless eclipse ecosystem edged editor educator effects eggnog elastic elbow elephant " +
  "elfishly elk elliptical elongated elusive elves emancipate embroidery emcee emerald emoticon emperor emulate " +
  "enactment enchilada endorphin energy engine enhance enigmatic enjoyably enlarged enormous enquirer ensemble " +
  "entryway enunciate envoy enzyme erasable ergonomic escalator espresso essay etching eternal ethics etiquette " +
  "eucalyptus evergreen evolution exam excerpt exerciser exfoliate exhale exquisite exterior exuberant fabric " +
  "factory faded failsafe falcon family fanfare fasten faucet favorite feasibly february federal feedback " +
  "feline fence ferret festival fettuccine fiberglass fiddle figurine fillet finalist fiscally fixture " +
  "flashlight flight florist flypaper foamless focus foggy folksong fondue footpath fossil fountain fox " +
  "fragment freeway fridge frosting fruit fryingpan gadget gainfully gamekeeper gangway garlic gathering " +
  "gearbox gecko gem generator geographer gerbil gesture geyser giddiness giftshop gigabyte giraffe giveaway " +
  "gizmo glasses gleeful glisten glove glycerin gnarly gnomish goatskin gong gooey gorgeous gosling gourmet " +
  "governor grape greyhound grill groundhog grumbling guacamole guitar gumdrop gurgling gusto gymnast gyration " +
  "habitat haiku halogen happiness hardhat hastily hatchling hazelnut headband hedgehog hefty helmet herbs " +
  "hexagon hubcap huddling hugeness hullabaloo hushing hyacinth hybrid hydrant icepack icing iconic identical " +
  "igloo ignition iguana illuminate imaging imitator imprint ionosphere iridescent iron irrigation isotope " +
  "issueless italicize itemizer itinerary ivory jabbering jackrabbit jaguar jalapeno jamboree janitor jasmine " +
  "jawbreaker jaywalker jazz jelly jersey jetski jiffy jigsaw jingling jobholder jogging joinable jokingly " +
  "journal jovial joystick jubilant juggle juice jujitsu jukebox jumpiness junkyard justifying juvenile kabob " +
  "kangaroo karate kayak keepsake kennel ketchup khaki kickstand kilogram kimono kingdom kiosk kite knapsack " +
  "kneecap koala krypton laboratory ladder lakefront lantern laptop lasagna latch laundry lavender lecturer " +
  "leftover leggings leisure lemon length leopard leprechaun lettuce levers library licorice lifeboat lightbulb " +
  "lilac limousine lint lioness lipstick liquid liverwurst lizard llama luau lucidity luggage lukewarm lullaby " +
  "lumberjack lunchbox luxurious lyrics macaroni maestro magazine mahogany majority makeover mammal mango " +
  "mapmaker marbles matchstick maximum mayonnaise mobilize moccasin moisture momentum monastery motorcycle " +
  "mousetrap movie mower mozzarella muckiness mudflow mule mundane mustard myriad myth nail namesake nanosecond " +
  "napkin narrator nautically navigate nectar negotiator nephew nest netting neuron nevermore nextdoor niece " +
  "nimbleness nirvana nugget nullify nursery nutcracker nylon oasis oat obediently observer obvious occupation " +
  "oceanic octopus ocular office oiliness older olympics omissible omnivorous oncoming onion onstage onward " +
  "onyx oomph opaquely opera opossum opponent optical opulently oscillator osmosis ostrich ovation oven owlish " +
  "oxford oxidize oxygen oyster ozone padlock pageant pajamas palm pamphlet paprika parakeet passport patio " +
  "pavement payphone pebble peculiarly pedometer pegboard pelican penguin peony pepperoni peroxide pewter " +
  "phonebook phrasing plank pledge plotted plug plywood poetic pogo poncho popcorn porcupine postcard poultry " +
  "powerboat prairie pretzel princess propeller prune pueblo pulley pumpkin punchbowl puppy purse pushup putt " +
  "puzzle quarters quesadilla quilt quote racoon radish ragweed railroad rampantly rarity raspberry rearrange " +
  "rebuilt receipt reentry refinery rehydrate reimburse rejoicing rekindle relic remote renovator reporter " +
  "reservoir retriever reunion rhapsody rhetoric rhino rhubarb rhyme ribbon rigidness rimmed riptide ritzy " +
  "riverboat roamer robe rocket romancer ropelike rotisserie roundtable royal rubber rudderless rugby rulebook " +
  "rummage running rustproof saddlebag saffron sainthood saltshaker sandworm sapphire sardine sassy satchel " +
  "sauna saxophone scarf scenario schoolbook scientist scooter scrapbook sculpture secretary seismology " +
  "selected semicolon sequence sesame settler shack shelf shirt shovel shrimp shuttle shyness sibling siesta " +
  "silicon simmering sisterhood sitcom sixfold sizable skateboard skies skylight sled sloth slumbering " +
  "smartphone smokestack smudge sneezing sniff snowsuit snugness speakers sphinx spider splashing sponge sprout " +
  "spur squirrel statue steamboat stingray strawberry student stylus suave subway suction suds suitcase " +
  "superstore surfer sushi swan sweatshirt swimwear sword sycamore syllable symphony systemize tablespoon taco " +
  "tadpole taekwondo tagalong takeout tallness tamale tapestry tarantula tastebud tavern thaw theater thimble " +
  "thorn thumb tiara tidbit tiebreaker timid tinsel tiptoeing tractor tree tripod trousers trucks tryout " +
  "tubeless tuesday tugboat tulip tumbleweed tupperware turtle tusk tutorial tuxedo tweezers twins umbrella " +
  "umpire uncle unevenness unflavored ungloved unicycle unlocking unmarked unnoticed unopened unpaved " +
  "unquenched unroll unusual unveiled unwrinkled unyielding unzip upbeat upcountry upfront upgrade upholstery " +
  "upkeep uppercut upright upstairs uptown upwind urban urchin usher utensil utility utmost utopia utterance " +
  "vacuum valuables vaporizer varied vegetable vehicle velcro vendor vestibule vicinity videogame viewfinder " +
  "vinegar violin viperfish visor vitamins vivacious vogue voicemail volleyball voucher voyage wagon wakeup " +
  "walrus wanderer water waving wheat whisper wholesaler wick wildcat windmill wired wishbone wizardry " +
  "wobbliness wolverine woolworker workbasket wrangle wristwatch xylophone yacht yard yearbook yesterday yodel " +
  "yogurt zebra zeppelin zestfully zigzagged zillion zipping zirconium zodiac zookeeper zucchini"
).split(" ");

export const HANDLE_QUARANTINE_SECONDS = 90 * 86400;
export const HANDLE_OPTIONS_PER_SPIN = 3;
export const HANDLE_SPINS_PER_CALL = 3;

function newHandle(): string {
  const [a, b] = crypto.getRandomValues(new Uint32Array(2));
  return `${HANDLE_WORDS[a % HANDLE_WORDS.length]} ${HANDLE_WORDS[b % HANDLE_WORDS.length]}`;
}

// A Handle is available when no User holds it and no User left it within the quarantine, so a
// stale name reaches nobody for a while. (?1 is the Handle.)
const AVAILABLE_SQL = `NOT EXISTS (SELECT 1 FROM users WHERE handle = ?1)
  AND NOT EXISTS (SELECT 1 FROM retired_handles WHERE handle = ?1 AND retired_at > unixepoch() - ${HANDLE_QUARANTINE_SECONDS})`;

/** Distinct random Handles available right now. Registration and renaming both draw here. */
export async function availableHandles(db: D1Database, count: number): Promise<string[]> {
  const chosen: string[] = [];
  for (let attempt = 0; attempt < count * 4 && chosen.length < count; attempt++) {
    const handle = newHandle();
    if (chosen.includes(handle) || handle.split(" ")[0] === handle.split(" ")[1]) continue;
    if (await db.prepare(`SELECT 1 AS ok WHERE ${AVAILABLE_SQL}`).bind(handle).first()) chosen.push(handle);
  }
  if (chosen.length < count) throw new Error("could not find unused Handles; try again");
  return chosen;
}

/**
 * Rename a User to a Handle they were offered. One transaction: the rename happens only if the
 * User still holds `current` and `next` is still available, and only then is `current`
 * quarantined. The User's identity (id, identity_hash) never changes.
 */
export async function renameUser(db: D1Database, user: number, current: string, next: string): Promise<boolean> {
  const [renamed] = await db.batch([
    db.prepare(`UPDATE users SET handle = ?1 WHERE id = ?2 AND handle = ?3 AND reaped_at IS NULL AND ${AVAILABLE_SQL}`)
      .bind(next, user, current),
    db.prepare(
      `INSERT INTO retired_handles (handle, user_id, retired_at) SELECT ?1, ?2, unixepoch()
        WHERE EXISTS (SELECT 1 FROM users WHERE id = ?2 AND handle = ?3)
       ON CONFLICT(handle) DO UPDATE SET user_id = excluded.user_id, retired_at = excluded.retired_at`,
    ).bind(current, user, next),
  ]);
  return renamed.meta.changes === 1;
}
