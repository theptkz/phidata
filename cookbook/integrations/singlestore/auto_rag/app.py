from typing import List

import streamlit as st
from phi.assistant import Assistant
from phi.document import Document
from phi.document.reader.pdf import PDFReader
from phi.document.reader.website import WebsiteReader
from phi.tools.streamlit.components import reload_button_sidebar
from phi.utils.log import logger

from assistant import get_assistant  # type: ignore

st.set_page_config(
    page_title="Autonomous RAG",
    page_icon=":orange_heart:",
)
st.title("Autonomous RAG with SingleStore")
st.markdown("##### :orange_heart: Built using [phidata](https://github.com/phidatahq/phidata)")


def restart_assistant():
    logger.debug("---*--- Restarting Assistant ---*---")
    st.session_state["ss_assistant"] = None
    st.session_state["ss_assistant_run_id"] = None
    if "url_scrape_key" in st.session_state:
        st.session_state["url_scrape_key"] += 1
    if "file_uploader_key" in st.session_state:
        st.session_state["file_uploader_key"] += 1
    st.rerun()


def main() -> None:
    # Get LLM Model
    selected_llm = st.sidebar.selectbox("Select Model", options=["GPT-4", "GPT-3.5", "Hermes2"])
    # Set llm in session state
    if "selected_llm" not in st.session_state:
        st.session_state["selected_llm"] = selected_llm
    # Restart the assistant if selected_llm changes
    elif st.session_state["selected_llm"] != selected_llm:
        st.session_state["selected_llm"] = selected_llm
        st.session_state["llm_updated"] = True
        restart_assistant()
    # Set chunk size based on selected_llm
    chunk_size = 2000 if selected_llm == "Hermes2" else 3000

    if "llm_updated" in st.session_state:
        st.sidebar.success(
            ":information_source: When changing LLM providers, please reload the knowledge base as the vector store table is updated."
        )
        del st.session_state["llm_updated"]

    # Check if web search is enabled
    web_search = st.sidebar.checkbox("Enable Web Search")
    if "web_search_enabled" not in st.session_state:
        st.session_state["web_search_enabled"] = web_search
    elif st.session_state["web_search_enabled"] != web_search:
        st.session_state["web_search_enabled"] = web_search
        restart_assistant()

    # Get the assistant
    ss_assistant: Assistant
    if "ss_assistant" not in st.session_state or st.session_state["ss_assistant"] is None:
        if "ss_assistant_run_id" in st.session_state:
            logger.info("---*--- Loading existing assistant ---*---")
            ss_assistant = get_assistant(
                model=selected_llm, run_id=st.session_state["ss_assistant_run_id"], web_search=web_search
            )
        else:
            logger.info("---*--- Creating new assistant ---*---")
            ss_assistant = get_assistant(model=selected_llm, web_search=web_search)
        st.session_state["ss_assistant"] = ss_assistant
    else:
        ss_assistant = st.session_state["ss_assistant"]

    # Create assistant run (i.e. log to database) and save run_id in session state
    try:
        st.session_state["ss_assistant_run_id"] = ss_assistant.create_run()
    except Exception:
        st.warning("Could not create assistant, is the database running?")
        return

    # Load existing messages
    ss_assistant_chat_history = ss_assistant.memory.get_chat_history()
    if len(ss_assistant_chat_history) > 0:
        logger.debug("Loading chat history")
        st.session_state["messages"] = ss_assistant_chat_history
    else:
        logger.debug("No chat history found")
        st.session_state["messages"] = [{"role": "assistant", "content": "Ask me anything..."}]

    # Prompt for user input
    if prompt := st.chat_input():
        st.session_state["messages"].append({"role": "user", "content": prompt})

    # Display existing chat messages
    for message in st.session_state["messages"]:
        if message["role"] == "system":
            continue
        with st.chat_message(message["role"]):
            st.write(message["content"])

    # If last message is from a user, generate a new response
    last_message = st.session_state["messages"][-1]
    if last_message.get("role") == "user":
        question = last_message["content"]
        with st.chat_message("ss_assistant"):
            response = ""
            resp_container = st.empty()
            for delta in ss_assistant.run(question):
                response += delta  # type: ignore
                resp_container.markdown(response)

            st.session_state["messages"].append({"role": "ss_assistant", "content": response})

    # Load knowledge base
    if ss_assistant.knowledge_base:
        # -*- Add websites to knowledge base
        if "url_scrape_key" not in st.session_state:
            st.session_state["url_scrape_key"] = 0

        input_url = st.sidebar.text_input(
            "Add URL to Knowledge Base", type="default", key=st.session_state["url_scrape_key"]
        )
        add_url_button = st.sidebar.button("Add URL")
        if add_url_button:
            if input_url is not None:
                alert = st.sidebar.info("Processing URLs...", icon="ℹ️")
                if f"{input_url}_scraped" not in st.session_state:
                    scraper = WebsiteReader(chunk_size=chunk_size, max_links=5, max_depth=1)
                    web_documents: List[Document] = scraper.read(input_url)
                    if web_documents:
                        ss_assistant.knowledge_base.load_documents(web_documents, upsert=True)
                    else:
                        st.sidebar.error("Could not read website")
                    st.session_state[f"{input_url}_uploaded"] = True
                alert.empty()

        # Add PDFs to knowledge base
        if "file_uploader_key" not in st.session_state:
            st.session_state["file_uploader_key"] = 100

        uploaded_file = st.sidebar.file_uploader(
            "Add a PDF :page_facing_up:", type="pdf", key=st.session_state["file_uploader_key"]
        )
        if uploaded_file is not None:
            alert = st.sidebar.info("Processing PDF...", icon="ℹ️")
            pdf_name = uploaded_file.name.split(".")[0]
            if f"{pdf_name}_uploaded" not in st.session_state:
                reader = PDFReader(chunk_size=chunk_size)
                pdf_documents: List[Document] = reader.read(uploaded_file)
                if pdf_documents:
                    ss_assistant.knowledge_base.load_documents(documents=pdf_documents, upsert=True)
                else:
                    st.sidebar.error("Could not read PDF")
                st.session_state[f"{pdf_name}_uploaded"] = True
            alert.empty()
            st.sidebar.success(":information_source: If the PDF throws an error, try uploading it again")

    if ss_assistant.storage:
        assistant_run_ids: List[str] = ss_assistant.storage.get_all_run_ids()
        new_assistant_run_id = st.sidebar.selectbox("Run ID", options=assistant_run_ids)
        if new_assistant_run_id is not None and st.session_state["ss_assistant_run_id"] != new_assistant_run_id:
            logger.info(f"---*--- Loading run: {new_assistant_run_id} ---*---")
            st.session_state["ss_assistant"] = get_assistant(
                model=selected_llm, run_id=new_assistant_run_id, web_search=web_search
            )
            st.rerun()

    assistant_run_name = ss_assistant.run_name
    if assistant_run_name:
        st.sidebar.write(f":thread: {assistant_run_name}")

    if st.sidebar.button("New Run"):
        restart_assistant()

    if st.sidebar.button("Auto Rename"):
        ss_assistant.auto_rename_run()

    if ss_assistant.knowledge_base:
        if st.sidebar.button("Clear Knowledge Base"):
            ss_assistant.knowledge_base.clear()

    # Show reload button
    reload_button_sidebar()


main()
