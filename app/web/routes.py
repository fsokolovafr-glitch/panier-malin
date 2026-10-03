from datetime import date
from uuid import uuid4

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.services.inventory import (
    INVENTORY_STATUSES,
    SHOPPING_STATUSES,
    add_purchase_to_inventory,
    decline_purchase_suggestion,
    get_purchase_suggestion,
    list_inventory,
    list_inventory_changes,
    list_shopping_items,
    move_inventory,
    save_inventory,
    save_shopping_item,
)
from app.services.purchases import (
    ValidationError,
    budget_summary,
    create_purchase,
    format_cents,
    format_quantity,
    list_purchases,
    overview_breakdown,
    set_budget,
    update_purchase,
)


web = Blueprint("web", __name__)


def database():
    return current_app.extensions["database"]


@web.app_template_filter("money")
def money_filter(value):
    return format_cents(value)


@web.app_template_filter("quantity")
def quantity_filter(value):
    return format_quantity(value)


@web.get("/")
def overview():
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    try:
        summaries = budget_summary(database(), month)
        categories, stores = overview_breakdown(database(), month)
    except ValidationError as error:
        flash(str(error), "error")
        return redirect(url_for("web.overview"))
    return render_template(
        "overview.html",
        active="overview",
        month=month,
        summaries=summaries,
        categories=categories,
        stores=stores,
        default_currency=current_app.config["DEFAULT_CURRENCY"],
    )


@web.post("/budget")
def save_budget():
    try:
        set_budget(
            database(),
            request.form.get("month"),
            request.form.get("currency"),
            request.form.get("amount"),
        )
        flash("Настройка бюджета сохранена.", "success")
    except ValidationError as error:
        flash(str(error), "error")
    return redirect(url_for("web.overview", month=request.form.get("month")))


@web.route("/add", methods=["GET", "POST"])
def add_purchase():
    if request.method == "POST":
        try:
            receipt_id, created = create_purchase(database(), request.form)
            if created:
                flash("Покупка сохранена.", "success")
                return redirect(url_for("web.purchase_home_suggestion", receipt_id=receipt_id))
            else:
                flash("Эта покупка уже была сохранена; дубль не создан.", "success")
            return redirect(url_for("web.purchases"))
        except ValidationError as error:
            flash(str(error), "error")
    return render_template(
        "add.html",
        active="add",
        idempotency_key=str(uuid4()),
        default_currency=current_app.config["DEFAULT_CURRENCY"],
        values=request.form,
    )


@web.get("/purchases")
def purchases():
    query = request.args.get("query", "")
    return render_template(
        "purchases.html",
        active="purchases",
        purchases=list_purchases(database(), query),
        query=query,
    )


@web.post("/purchases/<receipt_id>/edit")
def edit_purchase(receipt_id):
    try:
        update_purchase(database(), receipt_id, request.form)
        flash("Покупка обновлена, итоги пересчитаны.", "success")
    except ValidationError as error:
        flash(str(error), "error")
    return redirect(url_for("web.purchases", query=request.args.get("query", "")))


@web.get("/home")
def home_inventory():
    return render_template(
        "home.html",
        active="home",
        inventory=list_inventory(database()),
        changes=list_inventory_changes(database()),
        statuses=INVENTORY_STATUSES,
        idempotency_key=str(uuid4()),
    )


@web.post("/home/save")
def save_home_inventory():
    try:
        _inventory_id, changed = save_inventory(database(), request.form)
        flash(
            "Запас сохранён." if changed else "Это изменение уже было применено; дубль не создан.",
            "success",
        )
    except ValidationError as error:
        flash(str(error), "error")
    return redirect(url_for("web.home_inventory"))


@web.post("/home/<inventory_id>/move")
def move_home_inventory(inventory_id):
    try:
        changed = move_inventory(database(), inventory_id, request.form)
        flash(
            "Место хранения изменено." if changed else "Повтор не изменил запас.",
            "success",
        )
    except ValidationError as error:
        flash(str(error), "error")
    return redirect(url_for("web.home_inventory"))


@web.route("/purchases/<receipt_id>/home-suggestion", methods=["GET", "POST"])
def purchase_home_suggestion(receipt_id):
    suggestion = get_purchase_suggestion(database(), receipt_id)
    if not suggestion:
        flash("Покупка не найдена.", "error")
        return redirect(url_for("web.purchases"))
    if request.method == "POST":
        if request.form.get("decision") == "decline":
            decline_purchase_suggestion(database(), receipt_id)
            flash("Покупка не добавлена в домашние запасы.", "success")
            return redirect(url_for("web.purchases"))
        try:
            _inventory_id, changed = add_purchase_to_inventory(database(), receipt_id, request.form)
            flash(
                "Продукт добавлен домой." if changed else "Эта покупка уже была добавлена домой; запас не удвоен.",
                "success",
            )
            return redirect(url_for("web.home_inventory"))
        except ValidationError as error:
            flash(str(error), "error")
    return render_template(
        "home_suggestion.html",
        active="add",
        suggestion=suggestion,
        statuses=INVENTORY_STATUSES,
    )


@web.get("/shopping")
def shopping():
    return render_template(
        "shopping.html",
        active="shopping",
        groups=list_shopping_items(database()),
        statuses=SHOPPING_STATUSES,
    )


@web.post("/shopping/save")
def save_shopping():
    try:
        save_shopping_item(database(), request.form)
        flash("Список покупок обновлён.", "success")
    except ValidationError as error:
        flash(str(error), "error")
    return redirect(url_for("web.shopping"))
