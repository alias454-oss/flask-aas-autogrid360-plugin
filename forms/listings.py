# app/plugins/autogrid360/forms/listings.py
"""Listing forms for AutoGrid360."""

from datetime import datetime, timezone

from flask_wtf import FlaskForm
from wtforms import (
    DecimalField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
    widgets,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from app.plugins.autogrid360.services.reference import MODEL_OTHER_VALUE, normalize_reference_key


VIN_ALLOWED_CHARACTERS = frozenset("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")
YEAR_OTHER_VALUE = "__other__"
DOORS_OTHER_VALUE = "__other__"
STANDARD_MODEL_YEAR_MIN = 2000
AUTOMOTIVE_YEAR_MIN = 1886

CONDITION_CHOICES = [
    ("", "Not specified"),
    ("New", "New"),
    ("Used", "Used"),
    ("Certified Pre-Owned", "Certified Pre-Owned"),
    ("Project", "Project"),
    ("Salvage", "Salvage"),
    ("Rebuilt", "Rebuilt"),
    ("Other", "Other"),
]
TRANSMISSION_CHOICES = [
    ("", "Not specified"),
    ("Automatic", "Automatic"),
    ("Manual", "Manual"),
    ("CVT", "CVT"),
    ("Dual-Clutch", "Dual-Clutch"),
    ("Other", "Other"),
]
FUEL_TYPE_CHOICES = [
    ("", "Not specified"),
    ("Gasoline", "Gasoline"),
    ("Diesel", "Diesel"),
    ("Hybrid", "Hybrid"),
    ("Plug-in Hybrid", "Plug-in Hybrid"),
    ("Electric", "Electric"),
    ("Flex Fuel / E85", "Flex Fuel / E85"),
    ("Propane / LPG", "Propane / LPG"),
    ("Compressed Natural Gas", "Compressed Natural Gas"),
    ("Hydrogen", "Hydrogen"),
    ("Other", "Other"),
]
DOOR_CHOICES = [
    ("", "Not specified"),
    ("2", "2"),
    ("3", "3"),
    ("4", "4"),
    ("5", "5"),
    (DOORS_OTHER_VALUE, "Other..."),
]


def maximum_model_year() -> int:
    """Return the newest model year accepted by the listing editor."""

    return datetime.now(timezone.utc).year + 1


def model_year_choices() -> list[tuple[str, str]]:
    """Return ordinary model-year choices plus one explicit fallback."""

    choices = [("", "Not specified")]
    choices.extend(
        (str(year), str(year))
        for year in range(maximum_model_year(), STANDARD_MODEL_YEAR_MIN - 1, -1)
    )
    choices.append((YEAR_OTHER_VALUE, "Older / Other..."))
    return choices


def normalize_choice_token(value):
    """Preserve one stable structured choice token from submitted form data."""

    return str(value or "").strip()


def normalize_text(value):
    """Normalize user-entered text while preserving ordinary spacing."""

    if value is None:
        return None

    normalized = (
        str(value)
        .replace("\x00", "")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    return normalized or None


def normalize_vin(value):
    """Normalize optional seller-entered VIN text without claiming authenticity."""

    normalized = normalize_text(value)
    return normalized.upper() if normalized else None


class MultiCheckboxField(SelectMultipleField):
    """Render a multi-select reference field as ordinary checkboxes."""

    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class DeleteListingForm(FlaskForm):
    """Confirm deletion of a listing owned by the current seller."""

    submit = SubmitField("Delete Listing")


class SubmitListingForm(FlaskForm):
    """Submit a draft listing for moderation."""

    submit = SubmitField("Submit for Approval")


class ApproveListingForm(FlaskForm):
    """Approve one pending listing for publication."""

    submit = SubmitField("Approve Listing")


class RelistListingForm(FlaskForm):
    """Relist one expired listing through the current publication policy."""

    submit = SubmitField("Relist Listing")


class MarkSalePendingListingForm(FlaskForm):
    """Mark one public listing as having a buyer transaction in progress."""

    submit = SubmitField("Mark Sale Pending")


class MakeAvailableListingForm(FlaskForm):
    """Return Sale Pending or sold inventory to available status."""

    submit = SubmitField("Make Available Again")


class MarkSoldListingForm(FlaskForm):
    """Mark one available or Sale Pending listing as sold."""

    submit = SubmitField("Mark Sold")


class AdminListingStatusForm(FlaskForm):
    """Apply one explicit lifecycle state as an AutoGrid360 administrator."""

    status = SelectField(
        "Listing Status",
        choices=[
            ("draft", "Draft"),
            ("pending", "Pending Review"),
            ("active", "Active / Available"),
            ("sale_pending", "Sale Pending"),
            ("sold", "Sold"),
            ("expired", "Expired"),
            ("removed", "Removed"),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField("Change Status")


class ExpireListingForm(FlaskForm):
    """Expire one Active or Sale Pending listing as the system administrator."""

    submit = SubmitField("Expire Listing")


class RemoveListingForm(FlaskForm):
    """Remove one public or expired listing as its owner or system administrator."""

    submit = SubmitField("Remove Listing")


class ListingForm(FlaskForm):
    """Create the initial vehicle and marketplace listing record."""

    title = StringField(
        "Listing Title",
        validators=[DataRequired(), Length(max=120)],
        filters=[normalize_text],
    )
    price = DecimalField(
        "Price",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=10000)],
    )

    year = SelectField(
        "Year",
        choices=model_year_choices(),
        coerce=normalize_choice_token,
        validators=[Optional()],
    )
    year_other = StringField(
        "Other Model Year",
        filters=[normalize_text],
    )
    make = SelectField(
        "Make",
        choices=[],
        coerce=normalize_reference_key,
        validators=[DataRequired()],
    )
    model = SelectField(
        "Model",
        choices=[],
        coerce=normalize_choice_token,
        validators=[DataRequired()],
    )
    model_other = StringField(
        "Other / Unlisted Model",
        validators=[Length(max=80)],
        filters=[normalize_text],
    )
    trim = StringField(
        "Trim",
        validators=[Optional(), Length(max=80)],
        filters=[normalize_text],
    )
    vehicle_type = SelectField(
        "Vehicle Type",
        choices=[],
        coerce=normalize_reference_key,
        validators=[Optional()],
    )
    doors = SelectField(
        "Doors",
        choices=DOOR_CHOICES,
        coerce=normalize_choice_token,
        validators=[Optional()],
    )
    doors_other = IntegerField("Other Door Count")
    exterior_color = StringField(
        "Exterior Color",
        validators=[Optional(), Length(max=50)],
        filters=[normalize_text],
    )
    mileage = IntegerField(
        "Mileage",
        validators=[Optional(), NumberRange(min=0)],
    )
    condition = SelectField(
        "Condition",
        choices=CONDITION_CHOICES,
        coerce=normalize_choice_token,
        validators=[Optional()],
    )
    engine = StringField(
        "Engine",
        validators=[Optional(), Length(max=80)],
        filters=[normalize_text],
    )
    transmission = SelectField(
        "Transmission",
        choices=TRANSMISSION_CHOICES,
        coerce=normalize_choice_token,
        validators=[Optional()],
    )
    drivetrain = SelectField(
        "Drivetrain",
        choices=[],
        coerce=normalize_reference_key,
        validators=[Optional()],
    )
    features = MultiCheckboxField(
        "Features / Options",
        choices=[],
        coerce=normalize_reference_key,
        validators=[Optional()],
    )
    mpg = IntegerField(
        "MPG",
        validators=[Optional(), NumberRange(min=0, max=255)],
    )
    fuel_type = SelectField(
        "Fuel Type",
        choices=FUEL_TYPE_CHOICES,
        coerce=normalize_choice_token,
        validators=[Optional()],
    )
    vin = StringField(
        "VIN",
        validators=[Optional()],
        filters=[normalize_vin],
    )
    stock_number = StringField(
        "Stock Number",
        validators=[Optional(), Length(max=32)],
        filters=[normalize_text],
    )

    country_code = SelectField(
        "Country",
        choices=[],
        validators=[Optional()],
    )
    city = StringField(
        "City / Locality",
        validators=[Optional(), Length(max=100)],
        filters=[normalize_text],
    )
    zone_code = SelectField(
        "Region / Subdivision",
        choices=[],
        validators=[Optional()],
    )
    postal_code = StringField(
        "Postal / ZIP Code",
        validators=[Optional(), Length(max=20)],
        filters=[normalize_text],
    )

    def validate_year_other(self, field):
        """Require one real four-digit model year when the fallback is selected."""

        if self.year.data != YEAR_OTHER_VALUE:
            return
        value = str(field.data or "").strip()
        if len(value) != 4 or not value.isdigit():
            raise ValidationError("Enter a four-digit model year (YYYY).")
        year = int(value)
        if year < AUTOMOTIVE_YEAR_MIN or year > maximum_model_year():
            raise ValidationError(
                f"Model year must be between {AUTOMOTIVE_YEAR_MIN} "
                f"and {maximum_model_year()}."
            )

    def validate_doors_other(self, field):
        """Require a concrete door count when the fallback is selected."""

        if self.doors.data != DOORS_OTHER_VALUE:
            return
        if field.data is None:
            raise ValidationError("Enter the vehicle door count.")
        if field.data < 1 or field.data > 10:
            raise ValidationError("Door count must be between 1 and 10.")

    @property
    def resolved_year(self) -> int | None:
        """Return the database model year represented by the editor controls."""

        if not self.year.data:
            return None
        if self.year.data == YEAR_OTHER_VALUE:
            return int(self.year_other.data)
        return int(self.year.data)

    @property
    def resolved_doors(self) -> int | None:
        """Return the database door count represented by the editor controls."""

        if not self.doors.data:
            return None
        if self.doors.data == DOORS_OTHER_VALUE:
            return self.doors_other.data
        return int(self.doors.data)

    def set_vehicle_year(self, year: int | None) -> None:
        """Populate the editor controls from one persisted vehicle year."""

        if year is None:
            self.year.data = ""
            self.year_other.data = None
        elif STANDARD_MODEL_YEAR_MIN <= year <= maximum_model_year():
            self.year.data = str(year)
            self.year_other.data = None
        else:
            self.year.data = YEAR_OTHER_VALUE
            self.year_other.data = str(year)

    def set_vehicle_doors(self, doors: int | None) -> None:
        """Populate the editor controls from one persisted vehicle door count."""

        if doors is None:
            self.doors.data = ""
            self.doors_other.data = None
        elif doors in {2, 3, 4, 5}:
            self.doors.data = str(doors)
            self.doors_other.data = None
        else:
            self.doors.data = DOORS_OTHER_VALUE
            self.doors_other.data = doors

    def validate_vin(self, field):
        """Enforce the basic 17-character VIN alphabet when a VIN is supplied."""

        if not field.data:
            return
        if len(field.data) != 17:
            raise ValidationError("VIN must be exactly 17 characters.")
        if any(char not in VIN_ALLOWED_CHARACTERS for char in field.data):
            raise ValidationError(
                "VIN must contain only valid VIN letters and numbers; "
                "I, O, and Q are not allowed."
            )

    def validate_model_other(self, field):
        """Require fallback text only when the explicit unlisted model is selected."""

        if self.model.data == MODEL_OTHER_VALUE and not field.data:
            raise ValidationError("Enter the unlisted vehicle model.")

    submit = SubmitField("Save Draft")
