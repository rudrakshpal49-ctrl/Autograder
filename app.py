
import streamlit as st

from services.authentication import (
    authenticate_user,
    get_user_by_id,
)

from services.assignment_service import (
    create_assignment,
    get_all_assignments,
)


# ---------------------------------
# APPLICATION CONFIGURATION
# ---------------------------------

st.set_page_config(
    page_title="AutoGrader",
    page_icon="🎓",
    layout="wide",
)


# ---------------------------------
# SESSION INITIALIZATION
# ---------------------------------

if "user_id" not in st.session_state:
    st.session_state.user_id = None


# ---------------------------------
# LOGIN PAGE
# ---------------------------------

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


# ---------------------------------
# STUDENT DASHBOARD
# ---------------------------------

def student_dashboard(user):
    st.title("👨‍🎓 Student Dashboard")

    st.write(f"Welcome, {user['username']}!")

    st.subheader("Available Assignments")

    assignments = get_all_assignments()

    if not assignments:
        st.info("No assignments have been created yet.")

    else:
        for assignment in assignments:
            with st.expander(
                f"Assignment {assignment['id']}: "
                f"{assignment['title']}"
            ):
                st.write(assignment["description"])

    st.divider()

    st.subheader("C Program Submission")

    st.info(
        "Assignment selection and permanent C file "
        "submission will be implemented in the next stage."
    )


# ---------------------------------
# TEACHER DASHBOARD
# ---------------------------------

def teacher_dashboard(user):
    st.title("👩‍🏫 Teacher Dashboard")

    st.write(f"Welcome, {user['username']}!")

    tab1, tab2 = st.tabs([
        "Create Assignment",
        "View Assignments",
    ])

    with tab1:
        st.subheader("Create a New Assignment")

        with st.form("create_assignment_form"):
            title = st.text_input(
                "Assignment Title"
            )

            description = st.text_area(
                "Problem Statement",
                height=180,
            )

            submitted = st.form_submit_button(
                "Create Assignment"
            )

        if submitted:
            try:
                assignment_id = create_assignment(
                    teacher_id=user["id"],
                    title=title,
                    description=description,
                )

                st.success(
                    f"Assignment #{assignment_id} "
                    "created successfully!"
                )

            except ValueError as error:
                st.error(str(error))

            except PermissionError:
                st.error(
                    "You are not authorized to create assignments."
                )

    with tab2:
        st.subheader("Saved Assignments")

        assignments = get_all_assignments()

        if not assignments:
            st.info("No assignments have been saved yet.")

        else:
            for assignment in assignments:
                with st.expander(
                    f"Assignment {assignment['id']}: "
                    f"{assignment['title']}"
                ):
                    st.write(assignment["description"])


# ---------------------------------
# MAIN APPLICATION
# ---------------------------------

def main():
    if st.session_state.user_id is None:
        show_login()
        return

    user = get_user_by_id(st.session_state.user_id)

    if user is None:
        st.session_state.user_id = None
        st.rerun()

    st.sidebar.title("🎓 AutoGrader")

    st.sidebar.write(
        f"Logged in as: {user['username']}"
    )

    st.sidebar.write(
        f"Role: {user['role'].title()}"
    )

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
