"""
AI-drevet Weinstein-analyse pr. aktie ("Jarvis"-modulet).

Når en aktie vælges i dashboardet, giver dette modul en konkret vurdering af
price action ud fra Stan Weinsteins stage-model, baseret på tal der allerede
er beregnet i data_engine.py (stage, RS Rating, 30-ugers MA og dens
hældning, 12/52-ugers range, seneste 13F-aktivitet).

To tilstande:
  * "llm"        - Claude (Anthropic API) får de beregnede tal og skriver en
                    kort, konkret vurdering. Kræver `pip install anthropic`
                    og en gyldig API-credential (ANTHROPIC_API_KEY, en
                    `ant auth login`-profil, e.l.) samt internetadgang.
                    KOSTER PENGE PR. KALD (om end billigt og cachet).
  * "rule_based" - en deterministisk, gratis analysator der bruger nøjagtig
                    samme tal, skrevet som faste men datadrevne skabeloner
                    pr. stage. Altid tilgængelig, ingen afhængigheder.

`analyze()` prøver altid "llm" først og falder automatisk tilbage til
"rule_based" ved enhver fejl (intet API-kald muligt, ingen credential,
netværksfejl, rate limit, afvist anmodning, ...). Resultatet fortæller
altid hvilken kilde der blev brugt, og ved fallback hvorfor.
"""
import os

from providers import cache_get, cache_set

MODEL = os.environ.get("AI_ANALYST_MODEL", "claude-opus-5")
CACHE_TTL_SECONDS = 6 * 3600

SYSTEM_PROMPT = (
    "Du er en erfaren teknisk aktieanalytiker specialiseret i Stan Weinsteins "
    "stage-analyse-metode: Stage 1 (Basing/akkumulering), Stage 2 (Advancing/"
    "fremgang), Stage 3 (Topping/distribution), Stage 4 (Declining/nedtrend), "
    "vurderet ud fra kursen i forhold til dens 30-ugers glidende gennemsnit "
    "(MA) og MA'ens hældning. Du får en række allerede beregnede nøgletal for "
    "én aktie - regn ikke selv kurser om, brug tallene du får. "
    "Svar på dansk i 120-180 ord med tre dele: "
    "(1) hvilken stage aktien er i og hvorfor, ud fra de konkrete tal, "
    "(2) det vigtigste niveau at holde øje med lige nu (fx MA'en eller "
    "seneste high/low), "
    "(3) hvad der konkret ville bekræfte eller afkræfte stage-vurderingen. "
    "Vær specifik med tallene du får - ingen vage floskler. "
    "Dette er teknisk analyse, ikke finansiel rådgivning: anbefal aldrig at "
    "købe eller sælge. Hvis kursdata er markeret som simuleret, skal du "
    "nævne det til sidst."
)

_anthropic_client = None
_client_checked = False
_client_unavailable_reason = None


def _ensure_client():
    global _anthropic_client, _client_checked, _client_unavailable_reason
    if _client_checked:
        return _anthropic_client
    _client_checked = True
    try:
        import anthropic
    except ImportError:
        _client_unavailable_reason = "anthropic_not_installed"
        return None
    try:
        _anthropic_client = anthropic.Anthropic()
    except Exception as exc:
        _client_unavailable_reason = f"client_init_failed: {exc}"
        _anthropic_client = None
    return _anthropic_client


def status():
    """Best-effort status for /api/meta. Constructing the Anthropic client
    succeeds even with no credentials at all -- the SDK only resolves and
    validates them on the first real request -- so `package_installed` is
    the only thing this can honestly confirm up front without spending a
    real (paid) call. Whether a request will actually succeed is only known
    after `analyze()` tries one, surfaced per-ticker as `llm_unavailable_reason`."""
    client = _ensure_client()
    try:
        import anthropic  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {
        "package_installed": installed,
        "client_constructed": client is not None,
        "reason": None if client else _client_unavailable_reason,
    }


def build_prompt_facts(facts: dict) -> str:
    lines = [
        f"Aktie: {facts['ticker']} ({facts['name']}), sektor: {facts['sector']}",
        f"Kurs: {facts['price']} USD ({facts['day_chg_pct']:+.2f}% i dag, "
        f"{facts['week_chg_pct']:+.2f}% denne uge)",
        f"Beregnet stage (Weinstein): {facts['stage_label']}",
        f"RS Rating (IBD-stil, 1-99, 99=stærkest i universet): {facts['rs_rating']} "
        f"(var {facts['rs_rating_5d_ago']} for 5 handelsdage siden)",
        f"30-ugers glidende gennemsnit: {facts['ma30w']}, hældning seneste "
        f"10 uger: {facts['ma30w_slope_10w_pct']}%",
        f"12-ugers kursinterval: {facts['low_12w']} - {facts['high_12w']}",
        f"52-ugers kursinterval: {facts['low_52w']} - {facts['high_52w']}",
    ]
    if facts.get("recent_13f"):
        lines.append("Seneste institutionelle 13F-bevægelser i aktien:")
        for h in facts["recent_13f"]:
            lines.append(f"  - {h['filer']} ({h['quarter']}): {h['action']} ({h['change_pct']:+.1f}%)")
    if facts.get("data_source") == "simulated":
        lines.append("BEMÆRK: Kursdata for denne aktie er SIMULERET demo-data, ikke ægte markedsdata.")
    return "\n".join(lines)


def _try_llm_analysis(facts: dict):
    """Returns (result_dict, None) on success, or (None, error_reason) on any failure."""
    client = _ensure_client()
    if client is None:
        return None, _client_unavailable_reason

    import anthropic

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt_facts(facts)}],
        )
    except anthropic.AuthenticationError:
        return None, "invalid_api_key"
    except anthropic.RateLimitError:
        return None, "rate_limited"
    except anthropic.APIConnectionError:
        return None, "network_error"
    except anthropic.APIStatusError as exc:
        return None, f"api_error_{exc.status_code}"
    except Exception as exc:
        return None, f"unexpected_error: {exc}"

    if response.stop_reason == "refusal":
        return None, "refused"

    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), "").strip()
    if not text:
        return None, "empty_response"

    return {"source": "llm", "summary": text, "model": getattr(response, "model", MODEL)}, None


def rule_based_analysis(facts: dict) -> dict:
    """Deterministic, free, per-stage Weinstein write-up from the same computed facts."""
    stage = facts["stage"]
    slope = facts.get("ma30w_slope_10w_pct")
    slope_desc = "stigende" if slope and slope > 0 else "faldende" if slope and slope < 0 else "flad"
    rs = facts["rs_rating"]
    rs_prev = facts["rs_rating_5d_ago"]
    rs_trend = "styrkes" if rs >= rs_prev else "svækkes lidt"

    parts = [
        f"{facts['ticker']} befinder sig i {facts['stage_label']} ud fra kursen ({facts['price']}) "
        f"i forhold til det 30-ugers glidende gennemsnit ({facts['ma30w']}), som er {slope_desc} "
        f"({slope}% over de seneste 10 uger)."
    ]

    if stage == 2:
        parts.append(
            f"Det er det klassiske Stage 2-billede: kurs over et stigende MA. RS Rating på {rs} "
            f"(var {rs_prev} for 5 dage siden) {rs_trend}, hvilket understøtter billedet."
        )
        parts.append(
            f"Nøgleniveau: et brud under MA'en ({facts['ma30w']}) eller under 12-ugers "
            f"lavpunktet ({facts['low_12w']}) ville svække Stage 2 og pege mod Stage 3."
        )
    elif stage == 1:
        parts.append(
            f"Gennemsnittet flader ud, typisk for en base efter en nedtrend. RS Rating {rs} "
            f"viser {'begyndende relativ styrke' if rs >= 50 else 'fortsat svag relativ styrke'} "
            f"i forhold til resten af universet."
        )
        parts.append(
            f"Bekræftelse på overgang til Stage 2 kræver et brud over 12-ugers højden "
            f"({facts['high_12w']}) samtidig med at RS Rating fortsætter opad."
        )
    elif stage == 3:
        parts.append(
            f"Gennemsnittet flader ud eller vender efter en periode med kurs over MA - typisk "
            f"distribution efter en fremgang. RS Rating {rs} {rs_trend} vs. for 5 dage siden."
        )
        parts.append(
            f"Et brud under MA'en ({facts['ma30w']}) eller under 12-ugers lavpunktet "
            f"({facts['low_12w']}) ville bekræfte en overgang til Stage 4."
        )
    else:
        parts.append(
            f"Kursen ligger under et faldende MA - det klassiske Stage 4-nedtrend-billede. "
            f"RS Rating på {rs} er svag i universet-sammenhæng."
        )
        parts.append(
            f"En bund kan først bekræftes når kursen begynder at basere sig (Stage 1) omkring "
            f"et fladende gennemsnit. Aktuel 52-ugers bund: {facts['low_52w']}."
        )

    if facts.get("recent_13f"):
        last = facts["recent_13f"][-1]
        parts.append(
            f"Institutionel aktivitet: {last['filer']} {last['action'].lower()} sin position "
            f"i {last['quarter']} ({last['change_pct']:+.1f}%)."
        )

    if facts.get("data_source") == "simulated":
        parts.append("OBS: Denne analyse er baseret på SIMULERET demo-kursdata, ikke reelle markedsdata.")

    return {"source": "rule_based", "summary": " ".join(parts)}


def analyze(facts: dict) -> dict:
    result, error_reason = _try_llm_analysis(facts)
    if result:
        return result
    fallback = rule_based_analysis(facts)
    fallback["llm_unavailable_reason"] = error_reason
    return fallback


def analyze_cached(facts: dict) -> dict:
    fingerprint = f"{facts['ticker']}_{facts['stage']}_{facts['rs_rating']}_{round(facts['price'])}"
    cache_key = f"ai_analysis_{fingerprint}"
    cached = cache_get(cache_key, CACHE_TTL_SECONDS)
    if cached:
        return {**cached, "cached": True}
    result = analyze(facts)
    cache_set(cache_key, result)
    return {**result, "cached": False}
