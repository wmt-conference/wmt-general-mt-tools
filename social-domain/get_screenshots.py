import argparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
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
            "//button[normalize-space()='Show more'] | "
            "//a[normalize-space()='Show more'] | "
            "//button[normalize-space()='Show content'] | "
            "//a[normalize-space()='Show content'] | "
            "//button[contains(., 'Sensitive content')] | "
            "//button[contains(., 'Click to show')]"
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


def save_and_get_current_post(driver, output_path):
    focused_post = get_focused_post(driver)
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


def update_username_map(post, mapping=None, counter=1):
    mapping = dict(mapping or {})

    acct = post.get("account", {})
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

    
def render_html_to_image(data_folder, lang, output_path='output.png', anonymise=False, width=2000, height=15000):
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={width},{height}")
    options.add_argument(f"--lang={lang}")
    options.add_argument("--disable-extensions")
    options.add_argument("--dns-prefetch-disable")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = uc.Chrome(version_main=140, options=options)
    driver.set_page_load_timeout(120)

    all_files = [os.path.basename(x) for x in glob.glob(args.data_folder + "/*jsonl")]
    username2anon, counter = {}, 1
    for filename in all_files:
        with open(data_folder + '/' + filename, 'r') as f:
            for i, line in enumerate(f):
                # Define folder and filenames
                basename = filename.replace(".jsonl", "")
                os.makedirs(f"{data_folder}/{basename}/", exist_ok=True)
                filename_prefix = f"{data_folder}/{basename}/{basename}"
                
                # Skip if already downloaded
                #if os.path.exists(f"{filename_prefix}_extracted_{i}.png"):
                #    continue
                
                try:
                    data = json.loads(line)
                    if "uri" in data:
                        uri = data['uri']
                        driver.get(uri)
                        print(f">> Got URI: {uri}")

                        # Resize the window to match the full height (in practice, this can't actually be set
                        # to the full height at the moment, because it results in a timeout or other error)
                        scroll_height = driver.execute_script("return document.body.scrollHeight")
                        driver.set_window_size(width, min(height, scroll_height))

                        # Expand hidden text, show images (even sensitive) but hide alt text
                        expand_all_main_content(driver)

                        # For debugging (both lines can be commented out)
                        #save_fullwidth(driver, f"{filename_prefix}_{i}_fullwidth.png")
                        #save_without_margins(driver, f"{filename_prefix}_{i}.png")

                        #focused_post = get_focused_post(driver)
                        
                        if anonymise:
                            username2anon, counter = anonymise_post(driver, data, username2anon, counter, "anon-avatar.jpg")
                        
                        # Crop to get the current post
                        focused_post = save_and_get_current_post(driver, f"{filename_prefix}_extracted_{i}.png")

                        # Check that the focused post contains the text it should
                        if normalise_snippet(data['content'])[:30] not in normalise_visible(focused_post.text):
                            print(f"❌ Post {basename}_{i} does not seem to contain the expected text: {data['content'][:30]}")
                        
                except json.JSONDecodeError as e:
                    print(f"❌ Skipping invalid JSON line: {e}")

    driver.quit()
    
    
def main(args):
    render_html_to_image(args.data_folder, args.lang, anonymise=args.anonymise)


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
    args = parser.parse_args()
    main(args)
