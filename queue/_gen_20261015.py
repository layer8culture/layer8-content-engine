import json

D = "2026-10-15"
P = "20261015"
BASE = ("Create a cinematic Layer8Culture visual in a deep black environment with electric blue accent lighting, "
        "premium creator-tech atmosphere, late-night builder energy, documentary realism, human-centered AI themes, "
        "Black tech futurism, minimal composition, strong negative space, warm practical lighting, and high contrast. ")
NEG = (" Avoid generic startup office, bright corporate lighting, cheap gamer RGB, cluttered composition, cartoon props, "
       "excessive sci-fi armor, robots, fake hologram overload, random neon colors, copyrighted franchise references, "
       "garbled screen text, random figurines, tripod-mounted monitors, makeshift setups, rendered text, signage "
       "lettering, and garbled typography.")


def still(scene, lower=True):
    tail = " Leave the lower third as clean dark negative space." if lower else ""
    return BASE + scene + tail + NEG


def beats(a, b, c):
    return [{"text": a, "start": 0, "end": 2}, {"text": b, "start": 2, "end": 7}, {"text": c, "start": 7, "end": 10}]


def tt(n, time, cat, pillar, score, text, tags, scene, sora, bs, headline, secs=8):
    return {
        "id": f"{P}-layer8culture-tiktok-{n}", "account": "layer8culture", "category": cat,
        "platform": "tiktok", "format": "reel", "schedule_time": f"{D}T{time}:00-04:00",
        "text": text, "hashtags": tags, "hashtags_in_first_comment": True,
        "viral_pillar": pillar, "hook_score": score,
        "visual": {
            "source": "openai", "aspect": "9:16", "quality": "medium",
            "openai_prompt": still(scene, lower=False), "headline": headline,
            "reel": {"mode": "sora", "sora_prompt": sora + " Documentary realism, shallow depth of field, electric blue and deep navy palette, subtle ambient audio, no logos, no on-screen text, no copyrighted music. Vertical 9:16.",
                     "seconds": secs, "overlay_beats": beats(*bs)},
        },
    }


posts = []

posts.append(tt(
    1, "07:30", "AI Tool Experiments", "ai-fluency", 88,
    "Google just announced a work agent that can run for hours or days, keep context, and hand off tasks.\n\n"
    "Chat was the demo. This is the job.\n\nThe skill that matters now: knowing how to manage the agent.\n\n"
    "Follow Layer8Culture if you're building with AI.",
    ["#Layer8Culture", "#AI", "#AIagents", "#TechTok"],
    "An overhead night view of a vast dark city grid where thin electric-blue lines slowly connect rooftops like an agent graph, one lit window in the lower corner. No people in focus.",
    "Opening beat: a dark sleeping city seen from directly overhead, a single blue pulse of light fires across the grid. Then glowing agent-graph lines spread block to block, tasks handing off node to node while the city stays asleep, slow descending crane move, dawn warming the horizon at the end.",
    ("AI CLOCKS IN", "WHILE YOU SLEEP", "BE THE MANAGER"), "AI clocks in while you sleep"))

posts.append(tt(
    2, "11:30", "Livestream Clips", "build-in-public", 90,
    "24 hours live. Two builders. One realization: anything you can think of can be built.\n\n"
    "By 2am one of us was shipping a website solo while the other fought sleep.\n\n"
    "Follow Layer8Culture if you're building with AI.",
    ["#Layer8Culture", "#BuildInPublic", "#AI", "#TechTok"],
    "Extreme macro of a pair of hands on a mechanical keyboard in a dark room at night, blue light from a screen reflecting on the keys, a faint dawn glow through a window far behind in soft focus.",
    "Opening beat: macro of fast typing hands, a terminal cursor blinking, a merge confirmation flashing in blue reflection on the keys. Then a slow rack focus past the hands to a window where night turns to sunrise in a continuous time-lapse, coffee steam rising, a stack of finished work glowing.",
    ("24 HOURS LIVE", "IT CAN BE BUILT", "LOCK IN. BUILD."), "24 hours live. It can be built."))

posts.append(tt(
    3, "15:00", "AI Fluency Education", "black-tech-culture", 82,
    "Technology has seven layers. We're the eighth.\n\nThe human layer. The culture layer. The layer that decides what gets built and who gets to build it.\n\n"
    "Follow Layer8Culture if you're building with AI.",
    ["#Layer8Culture", "#Afrofuturism", "#TechCulture", "#AI"],
    "A luminous Afrofuturist skyline at golden hour, bold monumental architecture, a giant standalone figure-eight shaped monument glowing electric blue at the center, warm gold and electric blue light.",
    "Opening beat: seven stacked glowing horizontal layers of light hover over an Afrofuturist city at golden hour. Then a slow upward crane move rises through them to reveal an eighth layer shaped like a luminous standalone 8 monument over the skyline, warm gold meeting electric blue.",
    ("7 LAYERS EXIST", "WE'RE THE 8TH", "BUILD WITH US"), "Seven layers. We're the eighth."))

posts.append(tt(
    4, "18:30", "AI Fluency Education", "transformation", 86,
    "Watched 20 clips on a subject and feel like an expert?\n\nTwenty minutes isn't twenty hours. Install it. Break it. Fix it. Do it again.\n\nThat's how consumers become builders.\n\n"
    "Save this if you're done just watching AI happen.",
    ["#Layer8Culture", "#AIFluency", "#LearnInPublic", "#TechTok"],
    "A lone small silhouette walking away from a towering wall of dim, flickering rectangular screens toward a single bright open doorway filled with electric blue light, wide cinematic composition.",
    "Opening beat: a towering wall of dim scrolling screens flickers and cuts to black. Then a lone silhouette walks through the dark toward a bright doorway; as they cross the threshold the light shifts from cold passive blue to a focused warm-lit workspace, camera tracking behind then pushing past into dawn.",
    ("20 CLIPS?", "NOT SKILL", "BUILD 20 HOURS"), "20 clips isn't skill."))

posts.append(tt(
    5, "21:00", "AI Tool Experiments", "cinematic-coding", 80,
    "One chat is a conversation. A team of agents is a workflow.\n\nPlanner. Builder. Tester. Writer. Give each one a job and review the output like a lead.\n\n"
    "Follow @layer8culture if you're building with AI.",
    ["#Layer8Culture", "#AIagents", "#AgenticAI", "#TechTok"],
    "Abstract extreme macro of glass shards and light trails forming a branching org-chart structure in darkness, electric blue particles, reflective black surfaces, no people.",
    "Opening beat: a single glowing line of light stalls in black glass. Then it splits into four branching streams that pass handoffs of light between glass nodes, forming a clean branching structure, particles streaming, slow macro push-in and deep bass pulse.",
    ("STOP CHATTING", "BUILD A TEAM", "ORCHESTRATE"), "Stop chatting. Build a team."))

posts.append({
    "id": f"{P}-layer8culture-instagram-1", "account": "layer8culture", "category": "Livestream Clips",
    "platform": "instagram", "format": "reel", "schedule_time": f"{D}T12:15:00-04:00",
    "text": "We streamed for 24 hours and walked out with one belief: anything you can think of can be built.\n\n"
            "2am. One of us building a website alone. The other trying not to fall asleep.\n\n"
            "This is what happens when you stop watching AI and start building with it.\n\n"
            "Follow @layer8culture for the build.",
    "hashtags": ["#Layer8Culture", "#BuildInPublic", "#AIFluency", "#FutureBuilders", "#BuildWithAI", "#LearnInPublic",
                 "#AIBuilders", "#GenerativeAI", "#TechCreators", "#LiveStream", "#TechCulture"],
    "hashtags_in_first_comment": True,
    "visual": {"source": "reuse", "of": f"{P}-layer8culture-tiktok-2", "aspect": "9:16"},
})
posts.append({
    "id": f"{P}-layer8culture-instagram-2", "account": "layer8culture", "category": "AI Tool Experiments",
    "platform": "instagram", "format": "reel", "schedule_time": f"{D}T19:30:00-04:00",
    "text": "Google's new work agent, in plain English: an agent that runs for hours or days, keeps context, and delegates.\n\n"
            "Chat was the demo. This is the job.\n\nThe edge now isn't prompting. It's managing agents.\n\n"
            "Share this with a builder.",
    "hashtags": ["#Layer8Culture", "#AIagents", "#AgenticAI", "#AInews", "#AIFluency", "#BuildWithAI", "#Workflow",
                 "#Automation", "#GenerativeAI", "#AIBuilders"],
    "hashtags_in_first_comment": True,
    "visual": {"source": "reuse", "of": f"{P}-layer8culture-tiktok-1", "aspect": "9:16"},
})
posts.append({
    "id": f"{P}-layer8culture-youtube-1", "account": "layer8culture", "category": "Livestream Clips",
    "platform": "youtube", "format": "reel", "schedule_time": f"{D}T13:00:00-04:00",
    "youtube_title": "We Built for 24 Hours Straight: Anything Can Be Built #Shorts",
    "text": "24-hour livestream build recap: what two builders learned building with AI and a lot of focus.\n\n"
            "Follow Layer8Culture if you're building with AI.",
    "hashtags": ["#Shorts", "#Layer8Culture", "#BuildInPublic", "#AI", "#Hackathon"],
    "visual": {"source": "reuse", "of": f"{P}-layer8culture-tiktok-2", "aspect": "9:16"},
})
posts.append({
    "id": f"{P}-layer8culture-youtube-2", "account": "layer8culture", "category": "AI Tool Experiments",
    "platform": "youtube", "format": "reel", "schedule_time": f"{D}T20:30:00-04:00",
    "youtube_title": "Google's Work Agent: AI That Clocks In While You Sleep #Shorts",
    "text": "Google's persistent work agent runs for hours or days. Here's the builder skill that matters next: managing agents.\n\n"
            "Follow Layer8Culture if you're building with AI.",
    "hashtags": ["#Shorts", "#Layer8Culture", "#AIagents", "#AInews", "#AI"],
    "visual": {"source": "reuse", "of": f"{P}-layer8culture-tiktok-1", "aspect": "9:16"},
})


def slide(scene, headline, subtext=None, role="value", accent=None, kicker=None, footer=None):
    s = {"openai_prompt": still(scene), "headline": headline, "role": role}
    if subtext:
        s["subtext"] = subtext
    if accent:
        s["accent"] = accent
    if kicker:
        s["kicker"] = kicker
    if footer:
        s["footer"] = footer
    return s


CTA_SCENE = "A clean dark frame with a soft electric blue glow along the lower edge, minimal and calm."

posts.append({
    "id": f"{P}-layer8culture-instagram-3", "account": "layer8culture", "category": "AI Tool Experiments",
    "platform": "instagram", "format": "carousel", "schedule_time": f"{D}T08:30:00-04:00",
    "text": "Google work agent explained: an AI agent that runs for hours or days, remembers context, and delegates across tools.\n\n"
            "Why it matters: AI is moving from prompts into workflows.\n\n"
            "Try this week: pick one repeatable task, write it as steps, and hand it to an agent with one approval gate. "
            "That's how we run our own content engine.\n\n"
            "Swipe for the setup. Save this for your AI stack.",
    "hashtags": ["#Layer8Culture", "#AIagents", "#AgenticAI", "#AInews", "#AIFluency", "#BuildWithAI", "#Workflow",
                 "#Automation", "#GenerativeAI", "#AIBuilders", "#CreatorTech"],
    "hashtags_in_first_comment": True,
    "first_comment": "What's the one task you'd hand to an agent first?",
    "visual": {
        "source": "openai", "aspect": "1:1", "quality": "high",
        "typography_preset": "editorial_drop",
        "slides": [
            slide("A lone glowing terminal window floating in a dark studio, a long chain of electric blue light stretching from it into the distance like a workflow.", "Google's work agent changes the job", "Agents now run for hours, not seconds", "cover", "JOB", "AI TOOL DROP", "LAYER8CULTURE"),
            slide("A stream of light data flowing across a dark desk, hours passing as dawn light rises at the window.", "What changed", "A persistent agent that keeps context and delegates", kicker="01"),
            slide("Macro of a clean checklist of glowing nodes connected by thin blue lines on dark glass, handing off one to the next.", "What it actually does", "Runs multi-step work across tools, with permissions and audit logs", kicker="02"),
            slide("Two silhouettes at a late-night table reviewing a glowing board of tasks, warm lamp light, wide shot.", "Why builders care", "You stop prompting. You start managing.", kicker="03"),
            slide("Hands writing steps on paper beside a laptop, a single approval button glowing blue on screen.", "Try this this week", "One task. Written as steps. One approval gate.", kicker="04"),
            slide("A vast dark landscape with a single bright path of light leading to a horizon of rising dawn.", "The bigger signal", "From chat to workflow. Learn to orchestrate.", kicker="05"),
            slide(CTA_SCENE, "Save this for your AI stack", "Follow @layer8culture", "cta", footer="SHARE WITH A BUILDER"),
        ],
    },
})

posts.append({
    "id": f"{P}-layer8culture-instagram-4", "account": "layer8culture", "category": "Build in Public",
    "platform": "instagram", "format": "carousel", "schedule_time": f"{D}T17:00:00-04:00",
    "text": "What 24 hours of building live taught us about AI fluency.\n\n"
            "We locked in for a full day building with AI. The takeaway wasn't a tool. It was a mindset.\n\n"
            "4 lessons, straight from the stream. Save this for your next build.",
    "hashtags": ["#Layer8Culture", "#BuildInPublic", "#AIFluency", "#LearnInPublic", "#FutureBuilders", "#BuildWithAI",
                 "#CreatorTech", "#TechCulture", "#SideProject", "#AICommunity"],
    "hashtags_in_first_comment": True,
    "first_comment": "Have you ever locked in for 12+ hours on one build? What came out of it?",
    "visual": {
        "source": "openai", "aspect": "1:1", "quality": "medium",
        "typography_preset": "brand_title_card",
        "slides": [
            slide("A dark studio at night with a clock glowing on a wall and a laptop casting blue light, quiet and focused, wide composition.", "4 lessons from 24 hours live", "What we learned building with AI", "cover", "24"),
            slide("A dim path opening into a vast lit city, scale of possibility, cinematic wide shot.", "Anything you can think of can be built", "Think bigger. Then start.", kicker="01"),
            slide("Close view of a hand scribbling notes over a notebook beside a keyboard, warm lamp, shallow focus.", "Depth beats clips", "Twenty minutes is not twenty hours", kicker="02"),
            slide("Two chairs side by side in a dim studio facing a glowing screen, sense of company.", "Don't build in a silo", "Having someone guide you gets you past the freeze", kicker="03"),
            slide("A sunrise breaking across a window with a laptop still glowing in the foreground.", "Break it. Fix it. Repeat.", "That's the whole loop", kicker="04"),
            slide(CTA_SCENE, "Save this for your next build", "Follow @layer8culture", "cta"),
        ],
    },
})

posts.append({
    "id": f"{P}-layer8culture-instagram-5", "account": "layer8culture", "category": "Community Questions",
    "platform": "instagram", "format": "story", "schedule_time": f"{D}T20:00:00-04:00",
    "text": "It's Tech Thursday. If you had 24 hours locked in with AI, what would you build? Reply and tell us.",
    "hashtags": ["#Layer8Culture", "#TechThursday", "#BuildWithAI"],
    "visual": {
        "source": "openai", "aspect": "9:16", "quality": "medium",
        "openai_prompt": still("A vertical view of an open road of light leading from a dark foreground toward a glowing horizon, a sense of possibility, no people."),
        "headline": "24 hours. What do you build?", "subtext": "Reply and tell us", "accent": "BUILD",
        "overlay_position": "lower-center",
    },
})

json.dump(posts, open(f"queue/{D}.json", "w"), indent=2, ensure_ascii=False)
print(len(posts))
