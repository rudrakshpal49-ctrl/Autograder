
import streamlit as st

st.set_page_config(
    page_title="AutoGrader",
    page_icon="🎓",
    layout="wide"
)

st.sidebar.title("🎓 AutoGrader")

page = st.sidebar.radio(
    "Select Dashboard",
    ["Home", "Student", "Teacher/Admin"]
)

if page == "Home":
    st.title("Welcome to AutoGrader")
    st.success("AutoGrader is running successfully!")

    st.write(
        "An Explainable, Rule-Driven Evaluator for C Programs"
    )

elif page == "Student":
    st.title("Student Dashboard")

    uploaded_file = st.file_uploader(
        "Upload your C program",
        type=["c"]
    )

    if uploaded_file is not None:
        st.success("File selected successfully!")

        source_code = uploaded_file.getvalue().decode(
            "utf-8",
            errors="replace"
        )

        st.code(source_code, language="c")

elif page == "Teacher/Admin":
    st.title("Teacher / Admin Dashboard")

    st.text_input("Assignment Name")
    st.text_area("Problem Statement")

    st.info("Database integration will be added next.")
