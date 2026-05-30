import argparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
import time
import os
import html
import tempfile
import glob
import json
import requests
from io import BytesIO
from PIL import Image
import cv2
import numpy as np
from scipy.signal import find_peaks
import re, unicodedata
import base64
from urllib.parse import urlparse

# these margins may need to be adjusted (values seem to change depending on the setup)
margins = {'dark': {'left': 520, 'right': 826, 'top': 0},
           'light': {'left': 520, 'right': 826, 'top': 0}}

def wait_for_images(driver, scope=None, timeout=10):
    """
    Wait until all <img> tags in scope have non-zero naturalWidth.
    """
    scope = scope or driver
    end_time = time.time() + timeout
    while time.time() < end_time:
        unloaded = driver.execute_script("""
            const root = arguments[0] || document;
            const imgs = root.querySelectorAll("img");
            return Array.from(imgs).filter(img => !img.complete || img.naturalWidth === 0).length;
        """, scope if scope != driver else None)
        if unloaded == 0:
            return True
        time.sleep(0.5)
    return False

def wait_for_element_height_stable(driver, element, timeout=10, interval=0.3):
    end = time.time() + timeout
    last_height = None
    stable_rounds = 0

    while time.time() < end:
        height = driver.execute_script(
            "return Math.ceil(arguments[0].getBoundingClientRect().height);",
            element
        )

        if height == last_height:
            stable_rounds += 1
            if stable_rounds >= 3:
                return height
        else:
            stable_rounds = 0
            last_height = height

        time.sleep(interval)

    return last_height

def expand_all_main_content(driver):
    # Expand elements, show sensitive content and hide ALT overlays
    expand_hidden_elements(driver) # expand all elements to avoid hidden elements
    wait_for_images(driver)
    expand_sensitive_media(driver)
    driver.execute_script("document.querySelectorAll('.media-gallery__alt, .media-gallery__alt__label').forEach(el => el.style.display = 'none');") # hide ALT overlays
    time.sleep(5)

    
def expand_sensitive_media(driver, scope=None):
    """
    Click all 'Sensitive content / Click to show' buttons so images become visible.
    Works both at page-level and inside a specific container.
    """
    scope = scope or driver

    # Mastodon uses <button> overlays with text "Sensitive content" or "Click to show"
    buttons = scope.find_elements(
        By.XPATH,
        "//button[contains(., 'Sensitive content') or contains(., 'Click to show')]"
    )
    for btn in buttons:
        try:
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            try:
                btn.click()
            except:
                pass


            
def expand_hidden_elements(driver, scope=None, max_rounds=5, delay=1.0):
    """
    Expand all collapsed posts (CW + 'Show more') and sensitive media.
    Repeats until no expandable elements are left, or until max_rounds reached.
    """
    scope = scope or driver
    round_num = 0

    while round_num < max_rounds:
        round_num += 1
        expanded_any = False

        # Known selectors
        selectors = [
            "button[aria-expanded='false']",
            ".spoiler-link",
            ".status__content__spoiler-link",
            "button.spoiler-button",
            "button.sensitive-button",
            ".status__content__read-more-button",
        ]

        for sel in selectors:
            for el in scope.find_elements(By.CSS_SELECTOR, sel):
                try:
                    driver.execute_script("arguments[0].click();", el)
                    expanded_any = True
                except Exception:
                    try:
                        el.click()
                        expanded_any = True
                    except:
                        pass

        # Catch-all by visible text
        show_more_candidates = scope.find_elements(
            By.XPATH,
            ".//button[contains(normalize-space(.), 'Show more')] | "
            ".//a[contains(normalize-space(.), 'Show more')] | "
            ".//button[contains(normalize-space(.), 'Show content')] | "
            ".//a[contains(normalize-space(.), 'Show content')] | "
            ".//button[contains(normalize-space(.), 'Sensitive content')] | "
            ".//button[contains(normalize-space(.), 'Click to show')] | "
            ".//button[contains(normalize-space(.), 'and') and contains(normalize-space(.), 'more')] | "
            ".//a[contains(normalize-space(.), 'and') and contains(normalize-space(.), 'more')]"
        )
        for el in show_more_candidates:
            try:
                driver.execute_script("arguments[0].click();", el)
                expanded_any = True
            except Exception:
                try:
                    el.click()
                    expanded_any = True
                except:
                    pass

        if not expanded_any:
            break  # nothing left to expand
        time.sleep(delay)  # allow DOM to update before next round

    
def get_focused_post(driver):
    """
    Return the focused Mastodon post element.
    """
    return driver.find_element(By.CSS_SELECTOR, "article.is-focused, div.detailed-status")


def focused_post_contains(driver, snippet: str) -> bool:
    """
    Check if the focused post contains the given text snippet.
    """
    focused = get_focused_post(driver)
    post_text = focused.text
    return snippet in post_text

def normalise_snippet(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("<br />", "\n").replace("<br>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace and newlines for comparison
    text = re.sub(r"\s+", "", text).strip()
    text = unicodedata.normalize("NFC", text)
    return text

def normalise_visible(s: str) -> str:
    # Collapse whitespace and newlines for comparison
    s = re.sub(r"\s+", "", s)
    # Apply Unicode NFC normalisation
    s = unicodedata.normalize("NFC", s)
    return s.strip()


def save_fullwidth(driver, output_path):
    driver.save_screenshot(output_path)
    print(f"Saved full width image to {output_path}")


# detect whether the theme is mastodon dark or light (determines separator and margins)
def detect_mastodon_theme(image, sample_margin=100, sample_band=(20, 40), brightness_threshold=100):
    img_array = np.array(image.convert("RGB"))[:, :, ::-1]  # RGB → BGR
    gray = cv2.cvtColor(img_array, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    sample_area = gray[sample_margin:h-sample_margin, sample_band[0]:sample_band[1]]
    avg_brightness = np.mean(sample_area)
    theme = "dark" if avg_brightness < brightness_threshold else "light"
    return theme
    
def save_without_margins(driver, output_path):
    # Crop image (remove margins)
    png_data = driver.get_screenshot_as_png()
    im = Image.open(BytesIO(png_data))
    real_width, real_height = im.size
    
    # These margins may need to be adapted (behaviour changed recently)
    theme = detect_mastodon_theme(im)
    cropped = im.crop((margins[theme]['left'],margins[theme]['top'], real_width - margins[theme]['right'], real_height))
    cropped.save(f"{output_path}")
    print(f"Saved cropped image to {output_path}")

def is_bluesky_url(uri):
    return uri.startswith("https://bsky.app/")

def is_misskey_note_url(uri):
    return "/notes/" in uri

def get_bluesky_post_by_expected_text(driver, expected_text, timeout=20):
    expected = normalise_snippet(expected_text)[:30]

    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid*='postThreadItem']"))
    )

    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "[data-testid*='postThreadItem']"
    )

    for el in candidates:
        try:
            if expected and expected in normalise_visible(el.text):
                return el
        except Exception:
            pass

    raise NoSuchElementException("Could not find Bluesky post containing expected text")

def get_misskey_note_by_expected_text(driver, expected_text, timeout=20):
    expected = normalise_snippet(expected_text)[:30]

    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    candidates = driver.find_elements(
        By.CSS_SELECTOR,
        "article, [role='article'], div[class*='note'], div[class*='Note']"
    )

    matches = []

    for el in candidates:
        try:
            visible = normalise_visible(el.text)
            if expected and expected in visible:
                rect = driver.execute_script("""
                    const r = arguments[0].getBoundingClientRect();
                    return {
                        width: r.width,
                        height: r.height,
                        area: r.width * r.height
                    };
                """, el)

                if rect["width"] > 100 and rect["height"] > 50:
                    matches.append((rect["area"], el))
        except Exception:
            pass

    if not matches:
        raise NoSuchElementException("Could not find Misskey note containing expected text")

    # Pick the smallest matching element to avoid capturing the whole page.
    matches.sort(key=lambda x: x[0])
    return matches[0][1]

def save_and_get_current_post(driver, output_path, expected_text=None, uri=None):
    if uri and is_bluesky_url(uri):
        focused_post = get_bluesky_post_by_expected_text(driver, expected_text or "")

        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            focused_post
        )
        time.sleep(1)

        wait_for_images(driver, scope=focused_post)
        wait_for_element_height_stable(driver, focused_post)
        hide_bluesky_popovers(driver)

        focused_post.screenshot(f"{output_path}")
        print(f"Saved Bluesky post image to {output_path}")
        return focused_post

    if uri and is_misskey_note_url(uri):
        focused_post = get_misskey_note_by_expected_text(driver, expected_text or "")

        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            focused_post
        )
        time.sleep(1)

        wait_for_images(driver, scope=focused_post)
        wait_for_element_height_stable(driver, focused_post)

        focused_post.screenshot(f"{output_path}")
        print(f"Saved Misskey note image to {output_path}")
        return focused_post

    # Mastodon path
    focused_post = get_focused_post(driver)

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'start', inline: 'nearest'});",
        focused_post
    )
    time.sleep(0.5)

    expand_hidden_elements(driver, scope=focused_post)
    expand_sensitive_media(driver, scope=focused_post)
    wait_for_images(driver, scope=focused_post)
    wait_for_element_height_stable(driver, focused_post)

    focused_post.screenshot(f"{output_path}")
    print(f"Saved focused image to {output_path}")
    return focused_post

def image_to_data_uri(path):
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"

def replace_avatars_strict(driver, replacement_path, known_urls=None, rounds=3, delay=0.4):
    """
    Replace ONLY profile avatars (author / account avatars).
    - Touches <img> inside avatar containers and avatar elements with CSS background-image.
    - Explicitly skips emojis, reaction bars, media attachments, and post body images.
    - Runs multiple rounds to catch lazy-loaded items.
    """
    data_uri = image_to_data_uri(replacement_path)
    js = r"""
      const dataUri = arguments[0];
      const known   = arguments[1] || [];
      const matchUrl = u => {
        if (!known.length) return true;
        u = u || "";
        return known.some(k => u.includes(k));
      };
      let replaced = 0;

      // ---- <img> avatars inside known avatar containers
      const imgSel = [
        ".status__header .status__avatar img",
        ".status__avatar > img",
        ".detailed-status__display-avatar img",
        ".account__avatar > img",
        ".account__header__avatar img",
        "img.u-photo"               // microformats hook some themes use
      ].join(",");

      document.querySelectorAll(imgSel).forEach(img => {
        // exclude places that are NOT avatars
        if (img.closest(".status__content, .media-gallery, .media-attachments, .reactions, .emoji, .status-card")) return;

        const src    = img.getAttribute("src")    || "";
        const srcset = img.getAttribute("srcset") || "";
        if (src.startsWith("data:")) return;
        if (!matchUrl(src) && !matchUrl(srcset)) return;

        img.setAttribute("src", dataUri);
        img.removeAttribute("srcset");
        img.loading = "eager";
        replaced++;
      });

      // ---- avatars rendered via CSS background-image
      const bgSel = [
        ".status__header .status__avatar",
        ".detailed-status__display-avatar",
        ".account__avatar",
        ".account__header__avatar"
      ].join(",");

      document.querySelectorAll(bgSel).forEach(el => {
        if (el.closest(".status__content, .media-gallery, .media-attachments, .reactions, .emoji, .status-card")) return;
        const bg = getComputedStyle(el).backgroundImage;
        if (bg && bg !== "none") {
          el.style.backgroundImage = `url(${dataUri})`;
          replaced++;
        }
      });

      // also clear <picture><source srcset> used by some themes
      document.querySelectorAll("picture source").forEach(s => {
        const ss = s.getAttribute("srcset") || "";
        if (!known.length || matchUrl(ss)) { s.setAttribute("srcset", ""); replaced++; }
      });

      return replaced;
    """
    total = 0
    for _ in range(rounds):
        n = driver.execute_script(js, data_uri, known_urls or [])
        total += n
        if n == 0:
            break
        time.sleep(delay)
    return total

def hide_ui_noise(driver):
    driver.execute_script("""
        // Hide generic tooltips/popovers
        document.querySelectorAll('[role="tooltip"], .tooltip, .popover').forEach(el => {
            el.style.display = 'none';
        });

        // Hide elements whose text is about edits
        document.querySelectorAll('body *').forEach(el => {
            const t = (el.textContent || '').trim();
            if (
                t.startsWith('Last edited') ||
                /^Edited\\s+\\d+\\s+time/.test(t) ||
                /^Edited\\s+\\d+\\s+times/.test(t)
            ) {
                el.style.display = 'none';
            }
        });
    """)


from urllib.parse import urlparse

def update_username_map(post, mapping=None, counter=1):
    mapping = dict(mapping or {})

    acct = post.get("account", {})

    if isinstance(acct, str):
        try:
            acct = json.loads(acct)
        except json.JSONDecodeError:
            acct = {"username": acct, "acct": acct, "display_name": None}

    if not isinstance(acct, dict):
        acct = {}

    username = acct.get("username")
    acct_name = acct.get("acct")
    display = acct.get("display_name")
    url = acct.get("url")

    aliases = set()

    for x in [username, acct_name, display]:
        if x:
            aliases.add(x)
            aliases.add("@" + x)

    # Add full handle variants if possible
    if username and url:
        host = urlparse(url).netloc
        if host:
            aliases.add(f"{username}@{host}")
            aliases.add(f"@{username}@{host}")

    anon = None
    for key in aliases:
        if key in mapping:
            anon = mapping[key]
            break

    if anon is None and aliases:
        anon = f"anon_user_{counter}"
        counter += 1

    for key in aliases:
        if key and key not in mapping:
            mapping[key] = anon

    return mapping, counter
    
def update_username_map_old(post, mapping=None, counter=1):
    mapping = dict(mapping or {})

    acct = post.get("account", {})
    if isinstance(acct, str):
        try:
            acct = json.loads(acct)
        except json.JSONDecodeError:
            # Fallback if it is just a plain username/handle
            acct = {
                "username": acct,
                "acct": acct,
                "display_name": None,
            }

    if not isinstance(acct, dict):
        print(f"Warning: unexpected account field type: {type(acct)}")
        acct = {}

    username = acct.get("username")
    
    acct_name = acct.get("acct")       # full handle, sometimes includes domain
    display = acct.get("display_name")

    # pick first non-empty
    identifiers = [username, acct_name, display]

    anon = None
    for key in identifiers:
        if key and key in mapping:
            anon = mapping[key]
            break

    if anon is None and any(identifiers):
        anon = f"anon_user_{counter}"
        counter += 1

    for key in identifiers:
        if key and key not in mapping:
            mapping[key] = anon
            if " :bc:" in key:
                mapping[key.replace(" :bc:", "")] = anon
            else:
                mapping[key + " :bc:"] = anon

    return mapping, counter


def replace_user_identifiers_precise(driver, mapping):
    # 1. Replace text nodes anywhere in the page
    driver.execute_script("""
        const mapping = arguments[0];

        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            null
        );

        const nodes = [];
        while (walker.nextNode()) {
            nodes.push(walker.currentNode);
        }

        for (const node of nodes) {
            let txt = node.nodeValue;
            if (!txt) continue;

            let newTxt = txt;
            for (const [orig, anon] of Object.entries(mapping)) {
                if (orig) {
                    newTxt = newTxt.split(orig).join(anon);
                }
            }

            if (newTxt !== txt) {
                node.nodeValue = newTxt;
            }
        }
    """, mapping)

    # 2. Force-replace display-name containers as a fallback
    driver.execute_script("""
        const mapping = arguments[0];
        const sels = [
            '.display-name__html',
            'strong.display-name__html',
            '.display-name',
            '.account__display-name',
            '.status__display-name'
        ];

        sels.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                const txt = (el.textContent || '').trim();
                for (const [orig, anon] of Object.entries(mapping)) {
                    if (orig && txt.includes(orig)) {
                        el.textContent = txt.replace(orig, anon);
                    }
                }
            });
        });
    """, mapping)

    # 3. Handles / account names
    acct_els = driver.find_elements(By.CSS_SELECTOR, ".display-name__account, .account__acct, .username")
    for el in acct_els:
        try:
            txt = el.text.strip()
            new = txt
            for orig, anon in mapping.items():
                if orig and orig in new:
                    new = new.replace(orig, anon)
            if new != txt:
                driver.execute_script("arguments[0].textContent = arguments[1];", el, new)
        except Exception:
            pass


def replace_user_identifiers_precise_old(driver, mapping):
    # Display names: only change text nodes, keep emoji/badges (<img>/<svg>) intact
    display_els = driver.find_elements(By.CSS_SELECTOR, "strong.display-name__html")

    #import pdb; pdb.set_trace()
    for el in display_els:
        try:
            n_children = driver.execute_script("return arguments[0].childNodes.length;", el)
            for i in range(n_children):
                node_type = driver.execute_script("return arguments[0].childNodes[arguments[1]].nodeType;", el, i)
                if node_type == 3:  # TEXT_NODE
                    txt = driver.execute_script("return arguments[0].childNodes[arguments[1]].nodeValue;", el, i) or ""
                    new = txt
                    for orig, anon in mapping.items():
                        if orig and orig in new:
                            new = new.replace(orig, anon)
                    if new != txt:
                        driver.execute_script(
                            "arguments[0].childNodes[arguments[1]].nodeValue = arguments[2];",
                            el, i, new
                        )
        except Exception:
            print('pass')
            pass

    # Usernames / acct handles
    acct_els = driver.find_elements(By.CSS_SELECTOR, ".display-name__account, .account__acct, .username")
    for el in acct_els:
        try:
            txt = el.text.strip()
            new = txt
            for orig, anon in mapping.items():
                if orig and orig in new:
                    new = new.replace(orig, anon)
            if new != txt:
                driver.execute_script("arguments[0].textContent = arguments[1];", el, new)
        except Exception:
            pass
    

def anonymise_post(driver, post, mapping, counter, avatar_path):
    """
    Update mapping with username/display_name from `post`, 
    then anonymise the DOM: usernames, displaynames, avatars.

    Returns: (updated_mapping, next_counter)
    """
    # 1. update the mapping for this post
    mapping, counter = update_username_map(post, mapping, counter)

    # 2. replace usernames and display names in DOM
    replace_user_identifiers_precise(driver, mapping)

    # 3. replace avatars in DOM
    replace_avatars_strict(driver, avatar_path)

    return mapping, counter


def stitch_images_vertically(image_paths, output_path, padding=16, background=(255, 255, 255)):
    images = [Image.open(p).convert("RGB") for p in image_paths]

    #print("image paths = ", image_paths)
    
    if not images:
        print(f"⚠️  No images to stitch for {output_path}")
        return

    max_width = max(im.width for im in images)
    total_height = sum(im.height for im in images) + padding * (len(images) - 1)

    canvas = Image.new("RGB", (max_width, total_height), background)

    y = 0
    for im in images:
        x = (max_width - im.width) // 2
        canvas.paste(im, (x, y))
        y += im.height + padding

    canvas.save(output_path)
    print(f"✅ Saved stitched thread image to {output_path}")

    for im in images:
        im.close()

def normalise_post_url(uri):
    """
    Convert non-browser post URIs to browser URLs where possible.
    """
    bridgy_prefix = "https://bsky.brid.gy/convert/ap/"

    if uri.startswith(bridgy_prefix):
        at_uri = uri[len(bridgy_prefix):]

        # Example:
        # at://did:plc:xxx/app.bsky.feed.post/3abc
        m = re.match(r"at://([^/]+)/app\.bsky\.feed\.post/([^/]+)", at_uri)
        if m:
            did, rkey = m.groups()
            return f"https://bsky.app/profile/{did}/post/{rkey}"

    return uri

def hide_bluesky_popovers(driver):
    driver.execute_script("""
        document.querySelectorAll(
            '[role="menu"], [role="dialog"], [role="tooltip"], [data-testid*="popover"]'
        ).forEach(el => {
            el.style.display = 'none';
        });
    """)

def load_anon_state(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state.get("mapping", {}), state.get("counter", 1)

    return {}, 1


def save_anon_state(path, mapping, counter):
    state = {
        "mapping": mapping,
        "counter": counter,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        
def render_html_to_image(data_folder, lang, output_path='output.png', anonymise=False, all_image=False, width=2000, height=15000):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={width},{height}")
    options.add_argument(f"--lang={lang}")
    options.add_argument("--disable-extensions")
    options.add_argument("--dns-prefetch-disable")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = uc.Chrome(version_main=148, options=options)
    driver.set_page_load_timeout(120)

    all_files = [os.path.basename(x) for x in glob.glob(data_folder + "/*jsonl")]
    anon_state_path = os.path.join("anon_mapping.json")
    if anonymise:
        username2anon, counter = load_anon_state(anon_state_path)
    else:
        username2anon, counter = {}, 1

    try:
        for filename in all_files:
            basename = filename.replace(".jsonl", "")
            suffix = "-anon" if anonymise else ""
            os.makedirs(f"{data_folder}/{basename}/", exist_ok=True)
            filename_prefix = f"{data_folder}/{basename}/{basename}"

            if all_image:
                stitched_output_path = f"{filename_prefix}-all{suffix}.png"
                if os.path.exists(stitched_output_path):
                    print(f"Skipping existing stitched image: {stitched_output_path}")
                    continue

            thread_image_paths = []

            with open(data_folder + '/' + filename, 'r') as f:
                for i, line in enumerate(f):
                    # save the anonymisation dictionary
                    if anonymise:
                        save_anon_state(anon_state_path, username2anon, counter)

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"❌ Skipping invalid JSON line in {filename}:{i}: {e}")
                        continue

                    if "uri" not in data:
                        print(f"⚠️  Skipping {basename}_{i}: no URI")
                        continue

                    uri = normalise_post_url(data['uri'])

                    post_output_path = f"{filename_prefix}_{i}{suffix}.png"

                    # Skip rendering if the individual screenshot already exists.
                    # In --all_image mode, still add it so it can be stitched.
                    if os.path.exists(post_output_path):
                        print(f"Skipping existing image: {post_output_path}")
                        if all_image:
                            thread_image_paths.append(post_output_path)
                        continue

                    try:
                        driver.get(uri)
                        print(f">> Got URI: {uri}")
                        time.sleep(3)

                        # Expand first, then resize, because expansion/images can change page height.
                        # Expand Mastodon content only. On Bluesky this opens menus/popovers.
                        if not is_bluesky_url(uri) and not is_misskey_note_url(uri):
                            expand_all_main_content(driver)
                        else:
                            wait_for_images(driver)

                        scroll_height = driver.execute_script("""
                            return Math.max(
                                document.body.scrollHeight,
                                document.documentElement.scrollHeight
                            );
                        """)
                        driver.set_window_size(width, min(height, scroll_height + 1000))
                        time.sleep(1)

                        
                        if anonymise:
                            print(f">> About to anonymise")
                            username2anon, counter = anonymise_post(
                                driver,
                                data,
                                username2anon,
                                counter,
                                "anon-avatar.jpg"
                            )

                        hide_ui_noise(driver)

                        focused_post = save_and_get_current_post(
                            driver, post_output_path, expected_text=data.get("content", ""), uri=uri)

                        if all_image:
                            thread_image_paths.append(post_output_path)

                        if normalise_snippet(data['content'])[:30] not in normalise_visible(focused_post.text):
                            print(
                                f"❌ Post {basename}_{i} does not seem to contain the expected text: "
                                f"{data['content'][:30]}"
                            )

                    except (NoSuchElementException, TimeoutException, WebDriverException) as e:
                        print(f"⚠️  Skipping {basename}_{i}: could not locate/render focused post")
                        print(f"    URI: {uri}")
                        print(f"    Reason: {type(e).__name__}: {str(e).splitlines()[0]}")
                        continue
                    
                
            # This runs once after all posts in this JSONL file have been processed.
            if all_image:
                stitched_output_path = f"{filename_prefix}-all{suffix}.png"
                stitch_images_vertically(thread_image_paths, stitched_output_path)

    finally:
        driver.quit()
    
    
def main(args):
    render_html_to_image(args.data_folder, args.lang, anonymise=args.anonymise, all_image=args.all_image)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert JSONL files corresponding to Mastodon threads in a data folder to PNG outputs with the same base filename."
    )
    parser.add_argument(
        "data_folder",
        help="Path to the folder containing .jsonl files."
    )
    parser.add_argument(
        "--lang",
        "-l",
        default="en",
        help="Two-letter language code (default: 'en')."
    )
    parser.add_argument(
        "-a", "--anonymise",
        action="store_true",
        help="If set, usernames will be anonymised"
    )
    parser.add_argument(
        "--all-image",
        action="store_true",
        help="If set, save one stitched thread image named <id>-all.png instead of individual post images."
    )
    args = parser.parse_args()
    main(args)
