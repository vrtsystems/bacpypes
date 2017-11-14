#!/usr/bin/env python

"""
Local Schedule Object
"""

import sys
import calendar

from bacpypes.debugging import bacpypes_debugging, ModuleLogger
from bacpypes.consolelogging import ConfigArgumentParser

from bacpypes.core import run, deferred
from bacpypes.task import OneShotTask

from bacpypes.primitivedata import Atomic, Null, Integer, Unsigned, Real
from bacpypes.constructeddata import Array, ArrayOf, SequenceOf, AnyAtomic
from bacpypes.basetypes import DailySchedule, DeviceObjectPropertyReference, TimeValue
from bacpypes.object import register_object_type, get_datatype, WritableProperty, ScheduleObject

from bacpypes.app import BIPSimpleApplication
from bacpypes.service.device import LocalDeviceObject

# some debugging
_debug = 0
_log = ModuleLogger(globals())

#
#   match_date
#

def match_date(date, date_pattern):
    """
    Match a specific date, a four-tuple with no special values, with a date
    pattern, four-tuple possibly having special values.
    """
    # unpack the date and pattern
    year, month, day, day_of_week = date
    year_p, month_p, day_p, day_of_week_p = date_pattern

    # check the year
    if year_p == 255:
        # any year
        pass
    elif year != year_p:
        # specific year
        return False

    # check the month
    if month_p == 255:
        # any month
        pass
    elif month_p == 13:
        # odd months
        if (month % 2) == 0:
            return False
    elif month_p == 14:
        # even months
        if (month % 2) == 1:
            return False
    elif month != month_p:
        # specific month
        return False

    # check the day
    if day_p == 255:
        # any day
        pass
    elif day_p == 32:
        # last day of the month
        last_day = calendar.monthrange(year + 1900, month)[1]
        if day != last_day:
            return False
    elif day_p == 33:
        # odd days of the month
        if (day % 2) == 0:
            return False
    elif day_p == 34:
        # even days of the month
        if (day % 2) == 1:
            return False
    elif day != day_p:
        # specific day
        return False

    # check the day of week
    if day_of_week_p == 255:
        # any day of the week
        pass
    elif day_of_week != day_of_week_p:
        # specific day of the week
        return False

    # all tests pass
    return True

#
#   match_date_range
#

def match_date_range(date, date_range):
    """
    Match a specific date, a four-tuple with no special values, with a DateRange
    object which as a start date and end date.
    """
    return (date[:3] >= date_range.startDate[:3]) \
        and (date[:3] <= date_range.endDate[:3])

#
#   match_weeknday
#

def match_weeknday(date, weeknday):
    """
    Match a specific date, a four-tuple with no special values, with a
    BACnetWeekNDay, an octet string with three (unsigned) octets.
    """
    # unpack the date
    year, month, day, day_of_week = date
    last_day = calendar.monthrange(year + 1900, month)[1]

    # unpack the date pattern octet string
    if sys.version_info[0] == 2:
        weeknday_unpacked = [ord(c) for c in weeknday]
    elif sys.version_info[0] == 3:
        weeknday_unpacked = [c for c in weeknday]
    else:
        raise NotImplementedError("match_weeknday requires Python 2.x or 3.x")
    month_p, week_of_month_p, day_of_week_p = weeknday_unpacked

    # check the month
    if month_p == 255:
        # any month
        pass
    elif month_p == 13:
        # odd months
        if (month % 2) == 0:
            return False
    elif month_p == 14:
        # even months
        if (month % 2) == 1:
            return False
    elif month != month_p:
        # specific month
        return False

    # check the week of the month
    if week_of_month_p == 255:
        # any week
        pass
    elif week_of_month_p == 1:
        # days numbered 1-7
        if (day > 7):
            return False
    elif week_of_month_p == 2:
        # days numbered 8-14
        if (day < 8) or (day > 14):
            return False
    elif week_of_month_p == 3:
        # days numbered 15-21
        if (day < 15) or (day > 21):
            return False
    elif week_of_month_p == 4:
        # days numbered 22-28
        if (day < 22) or (day > 28):
            return False
    elif week_of_month_p == 5:
        # days numbered 29-31
        if (day < 29) or (day > 31):
            return False
    elif week_of_month_p == 6:
        # last 7 days of this month
        if (day < last_day - 6):
            return False
    elif week_of_month_p == 7:
        # any of the 7 days prior to the last 7 days of this month
        if (day < last_day - 13) or (day > last_day - 7):
            return False
    elif week_of_month_p == 8:
        # any of the 7 days prior to the last 14 days of this month
        if (day < last_day - 20) or (day > last_day - 14):
            return False
    elif week_of_month_p == 9:
        # any of the 7 days prior to the last 21 days of this month
        if (day < last_day - 27) or (day > last_day - 21):
            return False

    # check the day
    if day_of_week_p == 255:
        # any day
        pass
    elif day_of_week != day_of_week_p:
        # specific day
        return False

    # all tests pass
    return True

#
#   LocalScheduleObject
#

@bacpypes_debugging
@register_object_type(vendor_id=999)
class LocalScheduleObject(ScheduleObject):

    properties = [
        WritableProperty('presentValue', AnyAtomic),
        ]

    def __init__(self, **kwargs):
        if _debug: LocalScheduleObject._debug("__init__ %r", kwargs)
        ScheduleObject.__init__(self, **kwargs)

        # attach an interpreter task
        self._task = LocalScheduleInterpreter(self)

        # add some monitors
        for prop in ('weeklySchedule', 'exceptionSchedule', 'scheduleDefault'):
            self._property_monitors[prop].append(self._check_reliability)

        # check it now
        self._check_reliability()

    def _check_reliability(self, old_value=None, new_value=None):
        """This function is called when the object is created and after
        one of its configuration properties has changed.  The new and old value
        parameters are ignored, this is called after the property has been
        changed and this is only concerned with the current value."""
        if _debug: LocalScheduleObject._debug("_check_reliability %r %r", old_value, new_value)

        try:
            schedule_default = self.scheduleDefault

            if schedule_default is None:
                raise ValueError("scheduleDefault expected")
            if not isinstance(schedule_default, Atomic):
                raise TypeError("scheduleDefault must be an instance of an atomic type")

            if (self.weeklySchedule is None) and (self.exceptionSchedule is None):
                raise ValueError("schedule required")

            schedule_datatype = schedule_default.__class__
            if _debug: LocalScheduleObject._debug("    - schedule_datatype: %r", schedule_datatype)

            # check the weekly schedule values
            if self.weeklySchedule:
                for daily_schedule in self.weeklySchedule:
                    for time_value in daily_schedule.daySchedule:
                        if _debug: LocalScheduleObject._debug("    - daily time_value: %r", time_value)
                        if time_value is None:
                            pass
                        elif not isinstance(time_value.value, (Null, schedule_datatype)):
                            if _debug: LocalScheduleObject._debug("    - wrong type: expected %r, got %r",
                                schedule_datatype,
                                time_value.__class__,
                                )
                            raise TypeError("wrong type")
                        elif 255 in time_value.time:
                            if _debug: LocalScheduleObject._debug("    - wildcard in time")
                            raise ValueError("must be a specific time")

            # check the exception schedule values
            if self.exceptionSchedule:
                for special_event in self.exceptionSchedule:
                    for time_value in special_event.listOfTimeValues:
                        if _debug: LocalScheduleObject._debug("    - special event time_value: %r", time_value)
                        if time_value is None:
                            pass
                        elif not isinstance(time_value.value, (Null, schedule_datatype)):
                            if _debug: LocalScheduleObject._debug("    - wrong type: expected %r, got %r",
                                schedule_datatype,
                                time_value.__class__,
                                )
                            raise TypeError("wrong type")

            # check list of object property references
            obj_prop_refs = self.listOfObjectPropertyReferences
            if obj_prop_refs:
                for obj_prop_ref in obj_prop_refs:
                    obj_type = obj_prop_ref.objectIdentifier[0]

                    # get the datatype of the property to be written
                    datatype = get_datatype(obj_type, obj_prop_ref.propertyIdentifier)
                    if _debug: LocalScheduleObject._debug("    - datatype: %r", datatype)

                    if issubclass(datatype, Array) and (obj_prop_ref.propertyArrayIndex is not None):
                        if obj_prop_ref.propertyArrayIndex == 0:
                            datatype = Unsigned
                        else:
                            datatype = datatype.subtype
                        if _debug: LocalScheduleObject._debug("    - datatype: %r", datatype)

                    if datatype is not schedule_datatype:
                        if _debug: LocalScheduleObject._debug("    - wrong type: expected %r, got %r",
                            datatype,
                            schedule_datatype,
                            )
                        raise TypeError("wrong type")

            # all good
            self.reliability = 'noFaultDetected'
            if _debug: LocalScheduleObject._debug("    - no fault detected")

        except Exception as err:
            if _debug: LocalScheduleObject._debug("    - exception: %r", err)
            self.reliability = 'configurationError'

#
#   LocalScheduleInterpreter
#

@bacpypes_debugging
class LocalScheduleInterpreter(OneShotTask):

    def __init__(self, sched_obj):
        if _debug: LocalScheduleInterpreter._debug("__init__ %r", sched_obj)
        OneShotTask.__init__(self)

        # reference the schedule object to update
        self.sched_obj = sched_obj

        # add a monitor for the present value
        sched_obj._property_monitors['presentValue'].append(self.present_value_changed)

        # call to interpret the schedule
        deferred(self.process_task)

    def present_value_changed(self, old_value, new_value):
        """This function is called when the presentValue of the local schedule
        object has changed, both internally by this interpreter, or externally
        by some client using WriteProperty."""
        if _debug: LocalScheduleInterpreter._debug("present_value_changed %r %r %r", old_value, new_value)

    def process_task(self):
        if _debug: LocalScheduleInterpreter._debug("process_task(%s)", self.sched_obj.objectName)

        # check for a valid configuration
        if self.sched_obj.reliability != 'noFaultDetected':
            return

        # get the date and time from the device object
        current_date = self.sched_obj._app.localDevice.localDate
        if _debug: LocalScheduleInterpreter._debug("    - current_date: %r", current_date)

        current_time = self.sched_obj._app.localDevice.localTime
        if _debug: LocalScheduleInterpreter._debug("    - current_time: %r", current_time)

#
#   __main__
#

def main():
    global args

    # parse the command line arguments
    parser = ConfigArgumentParser(description=__doc__)

    # parse the command line arguments
    args = parser.parse_args()

    if _debug: _log.debug("initialization")
    if _debug: _log.debug("    - args: %r", args)

    # make a device object
    this_device = LocalDeviceObject(
        objectName=args.ini.objectname,
        objectIdentifier=('device', int(args.ini.objectidentifier)),
        maxApduLengthAccepted=int(args.ini.maxapdulengthaccepted),
        segmentationSupported=args.ini.segmentationsupported,
        vendorIdentifier=int(args.ini.vendoridentifier),
        )

    # make a sample application
    this_application = BIPSimpleApplication(this_device, args.ini.address)

    # get the services supported
    services_supported = this_application.get_services_supported()
    if _debug: _log.debug("    - services_supported: %r", services_supported)

    # let the device object know
    this_device.protocolServicesSupported = services_supported.value

    # make a schedule object with an integer value
    so1 = LocalScheduleObject(
        objectIdentifier=('schedule', 1),
        objectName='Schedule 1 (integer)',
        presentValue=Integer(8),
        weeklySchedule=ArrayOf(DailySchedule)([
            DailySchedule(
                daySchedule=[
                    TimeValue(time=(8,0,0,0), value=Integer(8)),
                    TimeValue(time=(14,0,0,0), value=Null()),
                    TimeValue(time=(17,0,0,0), value=Integer(42)),
                    TimeValue(time=(0,0,0,0), value=Null()),
                    ]),
            ] * 7),
        scheduleDefault=Integer(0),
        )
    _log.debug("    - so1: %r", so1)
    this_application.add_object(so1)

    so2 = LocalScheduleObject(
        objectIdentifier=('schedule', 2),
        objectName='Schedule 2 (real)',
        presentValue=Real(73.5),
        weeklySchedule=ArrayOf(DailySchedule)([
            DailySchedule(
                daySchedule=[
                    TimeValue(time=(9,0,0,0), value=Real(78.0)),
                    TimeValue(time=(10,0,0,0), value=Null()),
                    ]),
            ] * 7),
        scheduleDefault=Real(72.0),
        listOfObjectPropertyReferences=SequenceOf(DeviceObjectPropertyReference)([
            DeviceObjectPropertyReference(
                objectIdentifier=('analogValue', 1),
                propertyIdentifier='presentValue',
                ),
            ]),
        )
    _log.debug("    - so2: %r", so2)
    this_application.add_object(so2)

    # make sure they are all there
    _log.debug("    - object list: %r", this_device.objectList)

    _log.debug("running")

    run()

    _log.debug("fini")

if __name__ == "__main__":
    main()

