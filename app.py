
import streamlit as st

from services.authentication import (
    authenticate_user,
    get_user_by_id,
)


st.set_page_config(
    page_title="AutoGrader",
    page_icon="🎓",
    layout="wide",
)


# Initialize login session
if "user_id" not in st.session_state:
    st.session_state.user_id = None


def show_login():
    st.title("🎓 AutoGrader")
    st.subheader("Student and Teacher Login")

    with st.form("login_form"):
        username = st.text_input("Username")

        password = st.text_input(
            "Password",
            type="password",
        )

        submitted = st.form_submit_button("Login")

    if submitted:
        user = authenticate_user(username, password)

        if user is None:
            st.error("Invalid username or password.")
        else:
            st.session_state.user_id = user["id"]
            st.rerun()


def student_dashboard(user):
    st.title("👨‍🎓 Student Dashboard")
    st.write(f"Welcome, {user['username']}!")

    uploaded_file = st.file_uploader(
        "Upload your C program",
        type=["c"],
    )

    if uploaded_file is not None:
        source_code = uploaded_file.getvalue().decode(
            "utf-8",
            errors="replace",
        )

        st.success("File selected successfully!")
        st.code(source_code, language="c")

    st.info("Assignment submission will be connected next.")


def teacher_dashboard(user):
    st.title("👩‍🏫 Teacher Dashboard")
    st.write(f"Welcome, {user['username']}!")

    st.subheader("Assignment Management")
    st.text_input("Assignment Title")
    st.text_area("Problem Statement")

    st.info("Permanent assignment saving will be added in Stage 3B.")


def main():
    if st.session_state.user_id is None:
        show_login()
        return

    user = get_user_by_id(st.session_state.user_id)

    if user is None:
        st.session_state.user_id = None
        st.rerun()

    st.sidebar.title("🎓 AutoGrader")
    st.sidebar.write(f"Logged in as: {user['username']}")
    st.sidebar.write(f"Role: {user['role'].title()}")

    if st.sidebar.button("Logout"):
        st.session_state.user_id = None
        st.rerun()

    if user["role"] == "student":
        student_dashboard(user)

    elif user["role"] == "teacher":
        teacher_dashboard(user)

    else:
        st.error("Invalid account role.")


if __name__ == "__main__":
    main()
