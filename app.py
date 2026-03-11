import os
import re
import sqlite3
import datetime as dt
from io import BytesIO
import base64

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from gtts import gTTS
except Exception:
    gTTS = None

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "expressions.db")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sentences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_text TEXT,
            target_text TEXT,
            mp3_path TEXT,
            created_at TEXT,
            category TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dictionary(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT,
            meaning TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def sanitize_filename(text: str) -> str:
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")[:40]


def make_mp3_file(text: str, rid: int, lang: str = "ko") -> str:
    if not gTTS:
        raise RuntimeError("gTTS is not installed.")
    filename = f"{rid}_{sanitize_filename(text)}.mp3"
    path = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(path):
        tts = gTTS(text=text, lang=lang)
        tts.save(path)
    return path


def ensure_sentence_mp3(row: sqlite3.Row | dict, lang: str = "ko") -> str | None:
    text = row["source_text"] or ""
    rid = int(row["id"] or 0)
    existing = row["mp3_path"] or ""

    if existing:
        if os.path.isabs(existing) and os.path.exists(existing):
            return existing
        local_rel = os.path.join(BASE_DIR, existing)
        if os.path.exists(local_rel):
            return local_rel
        local_audio = os.path.join(AUDIO_DIR, os.path.basename(existing))
        if os.path.exists(local_audio):
            return local_audio

    try:
        new_path = make_mp3_file(text, rid, lang=lang)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE sentences SET mp3_path=? WHERE id=?", (new_path, rid))
        conn.commit()
        conn.close()
        return new_path
    except Exception:
        return None


def translate_ko_to_en(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if GoogleTranslator is None:
        return "Translator module is not installed. Please check requirements.txt."
    try:
        return GoogleTranslator(source="ko", target="en").translate(text)
    except Exception as e:
        return f"Translation error: {e}"


def import_from_excel(uploaded_file):
    df = pd.read_excel(uploaded_file)
    expected = ["Korean", "English", "mp3", "DateAdded", "Category"]
    for col in expected:
        if col not in df.columns:
            raise ValueError(f"Missing Excel column: {col}")

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for _, row in df.iterrows():
        cur.execute(
            """
            INSERT INTO sentences(source_text, target_text, mp3_path, created_at, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(row.get("Korean", "") or "").strip(),
                str(row.get("English", "") or "").strip(),
                str(row.get("mp3", "") or "").strip(),
                str(row.get("DateAdded", "") or "").strip(),
                str(row.get("Category", "") or "").strip(),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def export_to_excel_bytes() -> bytes:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT id, source_text AS Korean, target_text AS English, mp3_path AS mp3, created_at AS DateAdded, category AS Category FROM sentences ORDER BY id DESC",
        conn,
    )
    conn.close()
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="KED")
    buffer.seek(0)
    return buffer.getvalue()


def play_audio_n_times(audio_path: str, repeat_count: int = 1):
    if not audio_path or not os.path.exists(audio_path):
        st.warning("Audio file not found.")
        return

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    audio_base64 = base64.b64encode(audio_bytes).decode()

    html_code = f"""
    <audio id="player" controls autoplay style="width:100%;">
        <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
        Your browser does not support the audio element.
    </audio>
    <script>
    const player = document.getElementById("player");
    let count = 1;
    const repeatCount = {repeat_count};
    player.onended = function() {{
        if (count < repeatCount) {{
            count++;
            player.currentTime = 0;
            player.play();
        }}
    }};
    </script>
    """
    components.html(html_code, height=90)


def prepare_session_state():
    defaults = {
        "sentence_id": None,
        "sentence_korean": "",
        "sentence_english": "",
        "sentence_mp3": "",
        "sentence_category": "",
        "selected_wordbook_id": None,
        "wordbook_word": "",
        "wordbook_meaning": "",
        "pending_sentence_form": None,
        "pending_word_form": None,
        "pending_translation": None,
        "pending_sentence_reset": False,
        "pending_word_reset": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def apply_pending_updates():
    pending_sentence = st.session_state.get("pending_sentence_form")
    if pending_sentence:
        st.session_state["sentence_id"] = pending_sentence.get("sentence_id")
        st.session_state["sentence_korean"] = pending_sentence.get("sentence_korean", "")
        st.session_state["sentence_english"] = pending_sentence.get("sentence_english", "")
        st.session_state["sentence_mp3"] = pending_sentence.get("sentence_mp3", "")
        st.session_state["sentence_category"] = pending_sentence.get("sentence_category", "")
        st.session_state["pending_sentence_form"] = None

    pending_word = st.session_state.get("pending_word_form")
    if pending_word:
        st.session_state["selected_wordbook_id"] = pending_word.get("wordbook_id")
        st.session_state["wordbook_word"] = pending_word.get("word", "")
        st.session_state["wordbook_meaning"] = pending_word.get("meaning", "")
        st.session_state["pending_word_form"] = None

    pending_translation = st.session_state.get("pending_translation")
    if pending_translation is not None:
        st.session_state["sentence_english"] = pending_translation
        st.session_state["pending_translation"] = None

    if st.session_state.get("pending_sentence_reset"):
        st.session_state["sentence_id"] = None
        st.session_state["sentence_korean"] = ""
        st.session_state["sentence_english"] = ""
        st.session_state["sentence_mp3"] = ""
        st.session_state["sentence_category"] = ""
        st.session_state["pending_sentence_reset"] = False

    if st.session_state.get("pending_word_reset"):
        st.session_state["selected_wordbook_id"] = None
        st.session_state["wordbook_word"] = ""
        st.session_state["wordbook_meaning"] = ""
        st.session_state["pending_word_reset"] = False


def get_sentence_df(category: str = "All", search: str = "", limit: int = 100):
    conn = get_conn()
    query = "SELECT id, source_text, target_text, mp3_path, created_at, category FROM sentences"
    params = []
    clauses = []
    if category and category != "All":
        clauses.append("category = ?")
        params.append(category)
    if search:
        clauses.append("(source_text LIKE ? OR target_text LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def get_categories():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category FROM sentences WHERE category IS NOT NULL AND category <> '' ORDER BY category")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return ["All"] + rows


def save_sentence(sentence_id, kor, eng, mp3, category):
    conn = get_conn()
    cur = conn.cursor()
    now = dt.datetime.now().strftime("%Y-%m-%d")
    if sentence_id:
        cur.execute(
            "UPDATE sentences SET source_text=?, target_text=?, mp3_path=?, category=? WHERE id=?",
            (kor, eng, mp3, category, sentence_id),
        )
    else:
        cur.execute(
            "INSERT INTO sentences(source_text, target_text, mp3_path, created_at, category) VALUES (?, ?, ?, ?, ?)",
            (kor, eng, mp3, now, category),
        )
    conn.commit()
    conn.close()


def delete_sentence(sentence_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM sentences WHERE id=?", (sentence_id,))
    conn.commit()
    conn.close()


def get_wordbook_df():
    conn = get_conn()
    df = pd.read_sql_query("SELECT id, word, meaning FROM dictionary ORDER BY id DESC", conn)
    conn.close()
    return df


def save_word(wordbook_id, word, meaning):
    conn = get_conn()
    cur = conn.cursor()
    if wordbook_id:
        cur.execute("UPDATE dictionary SET word=?, meaning=? WHERE id=?", (word, meaning, wordbook_id))
    else:
        cur.execute("INSERT INTO dictionary(word, meaning) VALUES (?, ?)", (word, meaning))
    conn.commit()
    conn.close()


def delete_word(wordbook_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM dictionary WHERE id=?", (wordbook_id,))
    conn.commit()
    conn.close()


def apply_compact_css():
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1rem; padding-bottom: 1rem;}
        div[data-testid="stHorizontalBlock"] button {height: 2.8rem; font-size: 1rem;}
        .stTabs [data-baseweb="tab-list"] {gap: 0.2rem;}
        .stTabs [data-baseweb="tab"] {padding: 0.4rem 0.8rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sentence_editor():
    st.subheader("Add / Edit")
    korean = st.text_area("Korean", key="sentence_korean", height=100)
    english = st.text_area("English", key="sentence_english", height=100)
    c1, c2 = st.columns([2, 1])
    with c1:
        category = st.text_input("Category", key="sentence_category")
    with c2:
        st.text_input("MP3 Path", key="sentence_mp3", disabled=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Translate KO→EN", use_container_width=True, key="btn_translate"):
            st.session_state["pending_translation"] = translate_ko_to_en(korean)
            st.rerun()
    with c2:
        if st.button("Save", use_container_width=True, key="btn_save_sentence"):
            if not korean.strip():
                st.warning("Please enter a Korean sentence.")
            else:
                save_sentence(
                    st.session_state.get("sentence_id"),
                    korean.strip(),
                    english.strip(),
                    st.session_state.get("sentence_mp3", ""),
                    category.strip(),
                )
                st.session_state["pending_sentence_reset"] = True
                st.success("Saved successfully.")
                st.rerun()
    with c3:
        if st.button("Delete", use_container_width=True, key="btn_delete_sentence"):
            sid = st.session_state.get("sentence_id")
            if sid:
                delete_sentence(sid)
                st.session_state["pending_sentence_reset"] = True
                st.success("Deleted successfully.")
                st.rerun()
            else:
                st.warning("Please load a sentence first.")
    with c4:
        if st.button("Clear", use_container_width=True, key="btn_reset_sentence"):
            st.session_state["pending_sentence_reset"] = True
            st.rerun()

    st.divider()
    st.subheader("Audio for Selected Sentence")
    current_text = st.session_state.get("sentence_korean", "").strip()
    current_id = st.session_state.get("sentence_id") or 0

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Create MP3", use_container_width=True, key="btn_make_mp3"):
            if not current_text:
                st.warning("Please load or enter a sentence first.")
            else:
                try:
                    path = make_mp3_file(current_text, current_id or 999999, lang="ko")
                    st.session_state["sentence_mp3"] = path
                    if current_id:
                        conn = get_conn()
                        cur = conn.cursor()
                        cur.execute("UPDATE sentences SET mp3_path=? WHERE id=?", (path, current_id))
                        conn.commit()
                        conn.close()
                    st.success("MP3 created successfully.")
                    st.audio(path)
                except Exception as e:
                    st.error(f"MP3 creation failed: {e}")
    with c2:
        if st.button("Play Once", use_container_width=True, key="btn_editor_play_once"):
            path = st.session_state.get("sentence_mp3", "")
            if path and os.path.exists(path):
                play_audio_n_times(path, 1)
            elif current_text:
                path = ensure_sentence_mp3({"id": current_id or 999999, "source_text": current_text, "mp3_path": path}, lang="ko")
                if path:
                    st.session_state["sentence_mp3"] = path
                    play_audio_n_times(path, 1)
                else:
                    st.warning("Could not create MP3 for playback.")
            else:
                st.warning("Please load or enter a sentence first.")
    with c3:
        if st.button("Play 10 Times", use_container_width=True, key="btn_editor_play_repeat"):
            path = st.session_state.get("sentence_mp3", "")
            if path and os.path.exists(path):
                play_audio_n_times(path, 10)
            elif current_text:
                path = ensure_sentence_mp3({"id": current_id or 999999, "source_text": current_text, "mp3_path": path}, lang="ko")
                if path:
                    st.session_state["sentence_mp3"] = path
                    play_audio_n_times(path, 10)
                else:
                    st.warning("Could not create MP3 for playback.")
            else:
                st.warning("Please load or enter a sentence first.")


def render_sentence_list_and_player():
    st.subheader("Browse / Play")

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        category = st.selectbox("Category", get_categories(), key="filter_category")
    with c2:
        search = st.text_input("Search Sentences", key="sentence_search")
    with c3:
        limit = st.selectbox("Show", [50, 100, 200, 500], index=1, key="result_limit")

    df = get_sentence_df(category=category, search=search, limit=limit)
    if df.empty:
        st.info("No sentences found.")
        return

    labels = [f"[{r['id']}] {r['source_text'][:120]}{'...' if len(r['source_text']) > 120 else ''}" for _, r in df.iterrows()]
    index_map = dict(zip(labels, df.to_dict(orient="records")))
    selected_label = st.selectbox("Select Sentence", labels, key="selected_sentence_label")
    selected_row = index_map[selected_label]

    st.markdown("**Selected Full Sentence**")
    st.write(selected_row["source_text"])
    if selected_row.get("target_text"):
        st.caption(selected_row["target_text"])

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Load Sentence", use_container_width=True, key="btn_load_sentence_form"):
            st.session_state["pending_sentence_form"] = {
                "sentence_id": int(selected_row["id"]),
                "sentence_korean": selected_row["source_text"] or "",
                "sentence_english": selected_row["target_text"] or "",
                "sentence_mp3": selected_row["mp3_path"] or "",
                "sentence_category": selected_row["category"] or "",
            }
            st.rerun()
    with c2:
        if st.button("Play Once", use_container_width=True, key="btn_play_once"):
            path = ensure_sentence_mp3(selected_row, lang="ko")
            if path:
                play_audio_n_times(path, 1)
            else:
                st.warning("Playback failed.")
    with c3:
        if st.button("Play 10 Times", use_container_width=True, key="btn_play_repeat"):
            path = ensure_sentence_mp3(selected_row, lang="ko")
            if path:
                play_audio_n_times(path, 10)
            else:
                st.warning("Playback failed.")
    with c4:
        if st.button("Play Whole Category", use_container_width=True, key="btn_play_category_all"):
            rows = df.to_dict(orient="records")
            audio_sources = []
            for row in rows:
                path = ensure_sentence_mp3(row, lang="ko")
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        audio_sources.append(base64.b64encode(f.read()).decode())
            if not audio_sources:
                st.warning("No playable MP3 files found.")
            else:
                playlist_html = f"""
                <audio id="playlistPlayer" controls autoplay style="width:100%;"></audio>
                <script>
                const sources = [{','.join([repr('data:audio/mp3;base64,' + b64) for b64 in audio_sources])}];
                const player = document.getElementById('playlistPlayer');
                let idx = 0;
                function playIndex(i) {{
                    if (i >= sources.length) return;
                    player.src = sources[i];
                    player.play();
                }};
                player.onended = function() {{
                    idx += 1;
                    if (idx < sources.length) playIndex(idx);
                }};
                playIndex(0);
                </script>
                """
                components.html(playlist_html, height=100)

    display_df = df[["id", "source_text", "target_text", "category", "created_at"]].copy()
    display_df.columns = ["ID", "Korean", "English", "Category", "Date"]
    st.dataframe(display_df, use_container_width=True, height=320)


def render_excel_tools():
    st.subheader("Excel Import / Export")
    uploaded = st.file_uploader("Upload KED Excel File", type=["xlsx"], key="excel_upload")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Run Excel Import", use_container_width=True, key="btn_import_excel"):
            if uploaded is None:
                st.warning("Please choose an Excel file first.")
            else:
                try:
                    count = import_from_excel(uploaded)
                    st.success(f"Imported {count} sentences.")
                except Exception as e:
                    st.error(f"Import failed: {e}")
    with c2:
        excel_bytes = export_to_excel_bytes()
        st.download_button(
            "Export to Excel",
            data=excel_bytes,
            file_name="KED_export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="btn_export_excel",
        )


def render_word_search():
    st.subheader("Word Search")
    word = st.text_input("Enter Korean Word", key="search_word_input")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Search", use_container_width=True, key="btn_search_word"):
            if not word.strip():
                st.warning("Please enter a word.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("SELECT meaning FROM dictionary WHERE word=?", (word.strip(),))
                row = cur.fetchone()
                conn.close()
                if row:
                    st.session_state["wordbook_word"] = word.strip()
                    st.session_state["wordbook_meaning"] = row[0]
                    st.success(f"Meaning: {row[0]}")
                else:
                    meaning = translate_ko_to_en(word.strip())
                    st.session_state["wordbook_word"] = word.strip()
                    st.session_state["wordbook_meaning"] = meaning
                    if meaning.startswith("Translation error") or meaning.startswith("Translator module"):
                        st.error(meaning)
                    else:
                        st.success(f"Meaning: {meaning}")
    with c2:
        if st.button("Save to Wordbook", use_container_width=True, key="btn_add_wordbook_from_search"):
            word_val = st.session_state.get("wordbook_word", "").strip() or word.strip()
            meaning_val = st.session_state.get("wordbook_meaning", "").strip()
            if not word_val or not meaning_val:
                st.warning("Please search first and confirm the meaning.")
            else:
                save_word(None, word_val, meaning_val)
                st.success("Saved to wordbook.")
                st.rerun()

    if st.session_state.get("wordbook_word"):
        st.markdown("**Search Result**")
        st.write(f"- Word: {st.session_state.get('wordbook_word', '')}")
        st.write(f"- Meaning: {st.session_state.get('wordbook_meaning', '')}")


def render_wordbook():
    st.subheader("Wordbook")
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("Word", key="wordbook_word")
    with c2:
        st.text_input("Meaning", key="wordbook_meaning")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Save", use_container_width=True, key="btn_save_wordbook"):
            word = st.session_state.get("wordbook_word", "").strip()
            meaning = st.session_state.get("wordbook_meaning", "").strip()
            if not word or not meaning:
                st.warning("Please enter both word and meaning.")
            else:
                save_word(st.session_state.get("selected_wordbook_id"), word, meaning)
                st.success("Saved successfully.")
                st.session_state["pending_word_reset"] = True
                st.rerun()
    with c2:
        if st.button("Delete", use_container_width=True, key="btn_delete_wordbook"):
            wid = st.session_state.get("selected_wordbook_id")
            if wid:
                delete_word(wid)
                st.success("Deleted successfully.")
                st.session_state["pending_word_reset"] = True
                st.rerun()
            else:
                st.warning("Please select a word first.")
    with c3:
        if st.button("Clear", use_container_width=True, key="btn_reset_wordbook"):
            st.session_state["pending_word_reset"] = True
            st.rerun()

    df = get_wordbook_df()
    if not df.empty:
        labels = [f"[{r['id']}] {r['word']} = {r['meaning']}" for _, r in df.iterrows()]
        index_map = dict(zip(labels, df.to_dict(orient="records")))
        selected_label = st.selectbox("Select Word", labels, key="selected_word_label")
        selected_row = index_map[selected_label]
        st.write(selected_row["word"], "-", selected_row["meaning"])

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Load Selected Word", use_container_width=True, key="btn_load_wordbook_form"):
                st.session_state["pending_word_form"] = {
                    "wordbook_id": int(selected_row["id"]),
                    "word": selected_row["word"],
                    "meaning": selected_row["meaning"],
                }
                st.rerun()
        with c2:
            st.dataframe(df.rename(columns={"id": "ID", "word": "Word", "meaning": "Meaning"}), use_container_width=True, height=300)
    else:
        st.info("No wordbook data found.")


def main():
    st.set_page_config(page_title="KED (Korean Expression Dictionary)", page_icon="📗", layout="wide")
    init_db()
    prepare_session_state()
    apply_pending_updates()
    apply_compact_css()

    title_col, help_col = st.columns([3, 2])
    with title_col:
        st.title("📗 KED (Korean Expression Dictionary)")
    with help_col:

         st.info("""           
       ### How to Use
1. Choose a category in **Browse / Play**.  
2. Select a sentence and click **Load Sentence**.  
3. Learn with **Play Once / Play 10 Times / Play Whole Category**.  
4. If there is no MP3 yet, it will be created automatically during playback.  
5. Use **Add / Edit** to add or revise sentences.
            """
        )

    tab1, tab2, tab3 = st.tabs(["Expression Dictionary", "Word Search", "Wordbook"])

    with tab1:
        sub1, sub2, sub3 = st.tabs(["Add / Edit", "Browse / Play", "Excel"])
        with sub1:
            render_sentence_editor()
        with sub2:
            render_sentence_list_and_player()
        with sub3:
            render_excel_tools()

    with tab2:
        render_word_search()

    with tab3:
        render_wordbook()

    st.caption("After deployment, you can open this URL in Android Chrome and add it to the home screen like an app.")


if __name__ == "__main__":
    main()
