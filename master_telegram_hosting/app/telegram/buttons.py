from __future__ import annotations

from telethon.tl import types


RUNNABLE_FROM_STOPPED = {"APPROVED_STOPPED", "STOPPED", "ERROR"}
RUNNING_STATE = {"RUNNING"}
PENDING_STATE = {"PENDING_APPROVAL"}


def _style(kind: str, icon: str | None = None):
    return types.KeyboardButtonStyle(
        bg_primary=kind == "primary",
        bg_success=kind == "success",
        bg_danger=kind == "danger",
        icon=None,
    )


def cb(text: str, data: str, kind: str = "primary", icon: str | None = None):
    return types.KeyboardButtonCallback(text=text, data=data.encode("utf-8"), style=_style(kind, icon=icon))


def markup(rows: list[list[types.KeyboardButtonCallback]]):
    return types.ReplyInlineMarkup(rows=[types.KeyboardButtonRow(buttons=row) for row in rows])


def main_menu_markup(is_admin: bool = False):
    rows = [
        [cb("🚀 Deploy Node", "menu:upload", "success")],
        [cb("🗂️ My Servers", "menu:projects:0", "primary")],
        [
            cb("🌐 Node Status", "menu:node_status", "primary"),
            cb("💳 Billing & Plan", "menu:billing", "primary"),
        ],
        [cb("⚙️ Settings", "menu:settings", "primary")],
    ]
    if is_admin:
        rows.append([cb("🛡️ Admin Control", "admin:dashboard", "danger")])
    return markup(rows)


def projects_list_markup(project_ids: list[int], page: int, total_pages: int):
    rows: list[list] = []
    for project_id in project_ids:
        rows.append([cb(f"📟 Open Server #{project_id}", f"project:view:{project_id}", "primary")])
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"menu:projects:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"menu:projects:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("🏠 Command Deck", "menu:home", "primary")])
    return markup(rows)


def project_actions_markup(project_id: int, status_value: str, is_admin: bool = False):
    rows = []
    if status_value in RUNNABLE_FROM_STOPPED:
        rows.append([
            cb("▶️ Ignite Runtime", f"project:start:{project_id}", "success"),
            cb("🔄 Reboot Stack", f"project:restart:{project_id}", "primary"),
        ])
    elif status_value in RUNNING_STATE:
        rows.append([
            cb("⏹ Halt Runtime", f"project:stop:{project_id}", "danger"),
            cb("🔄 Reboot Stack", f"project:restart:{project_id}", "primary"),
        ])

    if is_admin and status_value in PENDING_STATE:
        rows.append([
            cb("✅ Approve Deploy", f"approval:approve:{project_id}:0", "success"),
            cb("❌ Reject Deploy", f"approval:reject:{project_id}:0", "danger"),
        ])

    rows.append([cb("📦 Modules & Packages", f"project:modules:{project_id}", "primary")])
    rows.append([
        cb("🗂 Files", f"project:files:{project_id}:0", "primary"),
        cb("🧪 Analysis", f"project:analysis:{project_id}", "primary"),
    ])
    rows.append([cb("📜 Logs", f"logs:view:{project_id}:stderr:all:0", "primary")])
    rows.append([cb("🗑️ Destroy Project", f"project:destroy:{project_id}", "danger")])
    if is_admin:
        rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    else:
        rows.append([cb("🗂️ Back to Servers", "menu:projects:0", "primary")])
    return markup(rows)


def project_destroy_confirm_markup(project_id: int, is_admin: bool = False):
    rows = [
        [cb("🗑️ Confirm Destruction", f"project:destroy_confirm:{project_id}", "danger")],
        [cb("↩️ Abort", f"project:view:{project_id}", "primary")],
    ]
    if is_admin:
        rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def approval_markup(project_id: int, queue_index: int, queue_total: int):
    rows = [
        [cb("✅ APPROVE", f"approval:approve:{project_id}:{queue_index}", "success")],
        [cb("❌ REJECT", f"approval:reject:{project_id}:{queue_index}", "danger")],
        [cb("🔍 VIEW ANALYSIS", f"approval:analysis:{project_id}:{queue_index}", "primary")],
        [cb("📜 Logs", f"logs:view:{project_id}:stderr:errors:0", "primary")],
    ]
    nav = []
    if queue_index > 0:
        nav.append(cb("⬅️ Prev", f"admin:approvals:{queue_index - 1}", "primary"))
    if queue_index < queue_total - 1:
        nav.append(cb("Next ➡️", f"admin:approvals:{queue_index + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def admin_dashboard_markup(force_subscribe_enabled: bool = False):
    force_label = "🛡️ Remove FSub" if force_subscribe_enabled else "🛡️ Set FSub"
    force_action = "admin:fsub:clear" if force_subscribe_enabled else "admin:fsub:set"
    return markup(
        [
            [cb("📊 Core Stats", "admin:dashboard", "primary")],
            [cb("🕒 Approval Queue", "admin:approvals:0", "primary")],
            [cb("👥 User Matrix", "admin:users:0", "primary")],
            [cb("📦 Project Matrix", "admin:projects_filter:all:0", "primary")],
            [cb("📣 Broadcast", "admin:broadcast", "success")],
            [cb(force_label, force_action, "danger" if force_subscribe_enabled else "primary")],
            [cb("🧾 Audit Stream", "admin:activity:0", "primary")],
            [cb("🖥 System Stats", "admin:stats", "primary")],
            [cb("🏠 Command Deck", "menu:home", "primary")],
        ]
    )


def admin_users_markup(user_ids: list[int], page: int, total_pages: int):
    rows = [[cb(f"👤 User #{user_id}", f"admin:user:{user_id}", "primary")] for user_id in user_ids]
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"admin:users:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"admin:users:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def admin_projects_markup(project_ids: list[int], status_filter: str, page: int, total_pages: int):
    rows = [
        [
            cb("All", "admin:projects_filter:all:0", "primary"),
            cb("Pending", "admin:projects_filter:pending:0", "primary"),
            cb("Running", "admin:projects_filter:running:0", "success"),
        ],
        [
            cb("Stopped", "admin:projects_filter:stopped:0", "primary"),
            cb("Error", "admin:projects_filter:error:0", "danger"),
            cb("Approved", "admin:projects_filter:approved:0", "success"),
        ],
    ]
    for project_id in project_ids:
        rows.append([cb(f"📦 Project #{project_id}", f"admin:project:{project_id}", "primary")])
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"admin:projects_filter:{status_filter}:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"admin:projects_filter:{status_filter}:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def admin_user_detail_markup(telegram_user_id: int, is_banned: bool, is_admin: bool):
    ban_action = cb("♻️ Unban User", f"admin:unban_user:{telegram_user_id}", "success") if is_banned else cb(
        "🚫 Ban User", f"admin:block_user:{telegram_user_id}", "danger"
    )
    admin_action = cb("⬇️ Demote Admin", f"admin:demote_user:{telegram_user_id}", "danger") if is_admin else cb(
        "⬆️ Promote Admin", f"admin:promote_user:{telegram_user_id}", "success"
    )
    return markup(
        [
            [ban_action],
            [admin_action],
            [cb("📁 User Projects", f"admin:user_projects:{telegram_user_id}:0", "primary")],
            [cb("👥 Back to Users", "admin:users:0", "primary")],
        ]
    )


def user_projects_markup(project_ids: list[int], telegram_user_id: int, page: int, total_pages: int):
    rows = [[cb(f"📦 Project #{project_id}", f"admin:project:{project_id}", "primary")] for project_id in project_ids]
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"admin:user_projects:{telegram_user_id}:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"admin:user_projects:{telegram_user_id}:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("👤 Back to User", f"admin:user:{telegram_user_id}", "primary")])
    return markup(rows)


def file_browser_markup(file_ids: list[int], project_id: int, page: int, total_pages: int):
    rows = [[cb(f"📄 File #{file_id}", f"file:view:{file_id}:0", "primary")] for file_id in file_ids]
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"project:files:{project_id}:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"project:files:{project_id}:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("📦 Back to Project", f"project:view:{project_id}", "primary")])
    return markup(rows)


def file_view_markup(file_id: int, project_id: int, page: int, total_pages: int, editable: bool):
    rows = []
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev Page", f"file:view:{file_id}:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next Page ➡️", f"file:view:{file_id}:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    if editable:
        rows.append([
            cb("✏️ Replace", f"file:edit:{file_id}", "success"),
            cb("🧩 Patch Lines", f"file:patch:{file_id}", "primary"),
        ])
    rows.append([cb("🗑️ Delete File", f"file:delete:{file_id}", "danger")])
    rows.append([cb("🗂 Back to Files", f"project:files:{project_id}:0", "primary")])
    return markup(rows)


def file_delete_confirm_markup(file_id: int, project_id: int):
    return markup(
        [
            [cb("🗑️ Confirm Delete", f"file:delete_confirm:{file_id}", "danger")],
            [cb("↩️ Abort", f"file:view:{file_id}:0", "primary")],
            [cb("📦 Project", f"project:view:{project_id}", "primary")],
        ]
    )


def patch_confirm_markup(file_id: int, project_id: int):
    return markup(
        [
            [cb("✅ Apply Patch", "file:patch_confirm", "success")],
            [cb("❌ Cancel Patch", "file:patch_cancel", "danger")],
            [cb("📄 Back to File", f"file:view:{file_id}:0", "primary")],
            [cb("📦 Project", f"project:view:{project_id}", "primary")],
        ]
    )


def logs_view_markup(project_id: int, stream: str, filter_mode: str, page: int, total_pages: int):
    rows = [
        [
            cb("stdout", f"logs:view:{project_id}:stdout:{filter_mode}:0", "primary"),
            cb("stderr", f"logs:view:{project_id}:stderr:{filter_mode}:0", "danger"),
        ],
        [
            cb("All lines", f"logs:view:{project_id}:{stream}:all:0", "primary"),
            cb("Errors/Warn", f"logs:view:{project_id}:{stream}:errors:0", "danger"),
        ],
    ]
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Newer", f"logs:view:{project_id}:{stream}:{filter_mode}:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Older ➡️", f"logs:view:{project_id}:{stream}:{filter_mode}:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("📦 Back to Project", f"project:view:{project_id}", "primary")])
    return markup(rows)


def activity_markup(page: int, total_pages: int):
    rows = []
    nav = []
    if page > 0:
        nav.append(cb("⬅️ Prev", f"admin:activity:{page - 1}", "primary"))
    if page < total_pages - 1:
        nav.append(cb("Next ➡️", f"admin:activity:{page + 1}", "primary"))
    if nav:
        rows.append(nav)
    rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def admin_user_quick_actions_markup(telegram_user_id: int):
    return markup(
        [
            [cb("🚫 Block", f"admin:block_user:{telegram_user_id}", "danger")],
            [cb("👤 Open Profile", f"admin:user:{telegram_user_id}", "primary")],
        ]
    )


def settings_markup(notifications_enabled: bool, default_runtime: str, is_admin: bool = False):
    rows = [
        [
            cb(
                f"🔔 Notifications: {'ON' if notifications_enabled else 'OFF'}",
                "settings:toggle_notifications",
                "success" if notifications_enabled else "danger",
            )
        ],
        [
            cb(
                f"🐍 Python {'●' if default_runtime == 'python' else ''}".strip(),
                "settings:set_runtime:python",
                "success" if default_runtime == 'python' else "primary",
            ),
            cb(
                f"🟩 Node.js {'●' if default_runtime == 'nodejs' else ''}".strip(),
                "settings:set_runtime:nodejs",
                "success" if default_runtime == 'nodejs' else "primary",
            ),
        ],
        [cb("🏠 Command Deck", "menu:home", "primary")],
    ]
    if is_admin:
        rows.append([cb("🛡️ Admin Control", "admin:dashboard", "primary")])
    return markup(rows)


def force_subscribe_gate_markup():
    return markup(
        [
            [cb("✅ I Joined — Recheck", "fsub:refresh", "success")],
            [cb("🏠 Command Deck", "menu:home", "primary")],
        ]
    )
