import json
import datetime
import gnome.basic_types
import gnome.map
import gnome.utilities.map_canvas
import numpy
import os
import time

from gnome.utilities.file_tools import haz_files

from pyramid.httpexceptions import HTTPNotFound
from pyramid.renderers import render
from pyramid.view import view_config

from webgnome.forms.movers import (
    AddMoverForm,
    WindMoverForm,
    DeleteMoverForm
)

from webgnome.forms.spills import DeleteSpillForm, AddSpillForm, PointReleaseSpillForm

from webgnome.forms.model import RunModelUntilForm, ModelSettingsForm
from webgnome.forms.movers import mover_form_classes
from webgnome.forms.spills import spill_form_classes
from webgnome.model_manager import WebWindMover, WebPointReleaseSpill
from webgnome.navigation_tree import NavigationTree
from webgnome import util


def _get_mover_forms(request, model):
    """
    Return a list of update and delete forms for the movers in ``model``.
    """ 
    update_forms = []
    delete_forms = []

    for mover in model.movers:
        delete_route = util.get_form_route(request, mover, 'delete')
        update_route = util.get_form_route(request, mover, 'update')

        if delete_route:
            delete_url = request.route_url(delete_route)
            delete_form = DeleteMoverForm(model=model, obj=mover)
            delete_forms.append((delete_url, delete_form))

        update_form_cls = mover_form_classes.get(mover.__class__, None)

        if update_route and update_form_cls:
            update_url = request.route_url(update_route, id=mover.id)
            update_form = update_form_cls(obj=mover)
            update_forms.append((update_url, update_form))

    return update_forms, delete_forms


def _get_spill_forms(request, model):
    """
    Return a list of update and delete forms for the spills in ``model``.
    
    TODO: Pretty similar to ``_get_mover_forms``.
    """
    update_forms = []
    delete_forms = []

    for spill in model.spills:
        delete_route = util.get_form_route(request, spill, 'delete')
        update_route = util.get_form_route(request, spill, 'update')

        if delete_route:
            delete_url = request.route_url(delete_route)
            delete_form = DeleteSpillForm(model=model, obj=spill)
            delete_forms.append((delete_url, delete_form))

        update_form_cls = spill_form_classes.get(spill.__class__, None)

        if update_route and update_form_cls:
            update_url = request.route_url(update_route, id=spill.id)
            update_form = update_form_cls(obj=spill)
            update_forms.append((update_url, update_form))

    return update_forms, delete_forms


@view_config(route_name='model_forms', renderer='gnome_json')
@util.json_require_model
def model_forms(request, model):
    """
    A partial view that renders all of the add and edit forms for ``model``,
    including settings, movers and spills.
    """
    context = {
        'run_model_until_form': RunModelUntilForm(),
        'run_model_until_form_url': request.route_url('run_model_until'),
        'settings_form': ModelSettingsForm(obj=model),
        'settings_form_url': request.route_url('model_settings'),
        'add_mover_form': AddMoverForm(),
        'add_spill_form': AddSpillForm(),
        'wind_mover_form': WindMoverForm(),
        'wind_mover_form_url': request.route_url('create_wind_mover'),
        'point_release_spill_form': PointReleaseSpillForm(),
        'point_release_spill_form_url': request.route_url(
            'create_point_release_spill'),
        'form_view_container_id': 'modal-container',
        'spill_update_forms': [],
        'spill_delete_forms': []
    }

    mover_update_forms, mover_delete_forms = _get_mover_forms(request, model)

    context['mover_update_forms'] = mover_update_forms
    context['mover_delete_forms'] = mover_delete_forms

    spill_update_forms, spill_delete_forms = _get_spill_forms(request, model)
    
    context['spill_update_forms'] = spill_update_forms
    context['spill_delete_forms'] = spill_delete_forms

    return {
        'html': render('model_forms.mak', context, request)
    }


@view_config(route_name='show_model', renderer='model.mak')
def show_model(request):
    """
    The entry-point for the web application. Load all forms and data
    needed to show a model.

    Get an existing :class:`gnome.model.Model` using the ``model_id`` field
    in the user's session or create a new one.

    If ``model_id`` was found in the user's session but the model did not
    exist, warn the user and suggest that they reload from a save file.
    """
    settings = request.registry.settings
    model_id = request.session.get(settings.model_session_key, None)
    model, created = settings.Model.get_or_create(model_id)
    data = {}

    if created:
        request.session[settings.model_session_key] = model.id
        if model_id:
            data['warning'] = 'The model you were working on is no longer ' \
                              'available. We created a new one for you.'

    data['map_bounds'] = []
    if model.map and model.map.map_bounds.any():
        data['map_bounds'] = model.map.map_bounds.tolist()

    data['model'] = model
    data['model_form_html'] = model_forms(request)['html']
    data['add_mover_form_id'] = AddMoverForm().id
    data['add_spill_form_id'] = AddSpillForm().id
    data['model_forms_url'] = request.route_url('model_forms')
    data['run_model_until_form_url'] = request.route_url('run_model_until')

    if model.time_steps:
        data['background_image_url'] = _get_model_image_url(
            request, model, 'background_map.png')
        data['generated_time_steps_json'] = json.dumps(
            model.time_steps, default=util.json_encoder)
        data['expected_time_steps_json'] = json.dumps(
            model.timestamps, default=util.json_encoder)

    return data


@view_config(route_name='create_model', renderer='gnome_json')
def create_model(request):
    """
    Create a new model for the user. Delete the user's current model if one
    exists.
    """
    settings = request.registry.settings
    model_id = request.session.get(settings.model_session_key, None)
    confirm = request.POST.get('confirm_new', None)

    if confirm:
        if model_id:
            settings.Model.delete(model_id)

        model = settings.Model.create()
        model_id = model.id
        request.session[settings.model_session_key] = model.id
        message = util.make_message('success', 'Created a new model.')
    else:
        message = util.make_message('error', 'Could not create a new model. '
                                             'Invalid data was received.')

    return {
        'model_id': model_id,
        'message': message
    }

def _render_model_settings(request, form):
    context = {
        'form': form,
        'action_url': request.route_url('model_settings')
    }

    return {
        'form_html': render(
            'webgnome:templates/forms/model_settings.mak', context)
    }


def _model_settings_post(request, model):
    form = ModelSettingsForm(request.POST or None)

    if form.validate():
        date = form.date.data

        model.time_step = datetime.timedelta(
            seconds=form.computation_time_step.data)

        model.start_time = datetime.datetime(
            day=date.day, month=date.month, year=date.year,
            hour=form.hour.data, minute=form.minute.data,
            second=0)

        model.duration = datetime.timedelta(
            days=form.duration_days.data, hours=form.duration_hours.data)

        model.uncertain = form.uncertain.data

        # TODO: show_currents, prevent_land_jumping, run_backwards options.

        return {
            'form_html': None
        }

    return _render_model_settings(request, form)


@view_config(route_name='model_settings', renderer='gnome_json')
@util.json_require_model
def model_settings(request, model):
    if request.method == 'POST':
        return _model_settings_post(request, model)

    form = ModelSettingsForm(obj=model)

    return _render_model_settings(request, form)



def _get_model_image_url(request, model, filename):
    return request.static_url('webgnome:static/%s/%s/%s/%s' % (
        request.registry.settings['model_images_url_path'],
        model.id,
        model.runtime,
        filename))


def _get_timestamps(model):
    """
    TODO: Move into ``gnome.model.Model``?
    """
    timestamps = []

    # XXX: Why is _num_time_steps a float? Is this ok?
    for step_num in range(int(model._num_time_steps) + 1):
        if step_num == 0:
            dt = model.start_time
        else:
            delta = datetime.timedelta(seconds=step_num * model.time_step)
            dt = model.start_time + delta
        timestamps.append(dt)

    return timestamps


def _get_time_step(request, model):
    step = None

    model_dir = os.path.join(
        request.registry.settings['model_images_dir'], str(model.id))
    images_dir = os.path.join(model_dir, model.runtime)

    if not os.path.exists(model_dir):
        os.mkdir(model_dir)

    if not os.path.exists(images_dir):
        os.mkdir(images_dir)

    try:
        curr_step, file_path, timestamp = model.next_image(images_dir)
        filename = file_path.split(os.path.sep)[-1]
        image_url = _get_model_image_url(request, model, filename)

        step = {
            'id': curr_step,
            'url': image_url,
            'timestamp': timestamp
        }
    except StopIteration:
        pass

    return step


def _make_runtime():
    return time.strftime("%Y-%m-%d-%H-%M-%S")


@view_config(route_name='run_model', renderer='gnome_json')
@util.json_require_model
def run_model(request, model):
    """
    Start a run of the user's current model and return a JSON object
    containing the first time step.
    """
    data = {}

    # TODO: This should probably be a method on the model.
    timestamps = _get_timestamps(model)
    data['expected_time_steps'] = timestamps
    model.timestamps = timestamps
    model.uncertain = True

    if not model.runtime:
        model.runtime = _make_runtime()

    # TODO: Set separately in spill view.
    if not model.spills:
        spill = WebPointReleaseSpill(
            name="Long Island Spill",
            num_LEs=1000,
            start_position=(-72.419992, 41.202120, 0.0),
            release_time=model.start_time)

        model.add_spill(spill)

    if not model.movers:
        start_time = model.start_time

        r_mover = gnome.movers.RandomMover(diffusion_coef=500000)
        model.add_mover(r_mover)

        series = numpy.zeros((5,), dtype=gnome.basic_types.datetime_value_2d)
        series[0] = (start_time, (30, 50) )
        series[1] = (start_time + datetime.timedelta(hours=18), (30, 50))
        series[2] = (start_time + datetime.timedelta(hours=30), (20, 25))
        series[3] = (start_time + datetime.timedelta(hours=42), (25, 10))
        series[4] = (start_time + datetime.timedelta(hours=54), (25, 180))

        w_mover = WebWindMover(timeseries=series, is_constant=False,
                               units='mps')
        model.add_mover(w_mover)


    # TODO: Set separately in map configuration form/view.
    if not model.map:
        map_file = os.path.join(
            request.registry.settings['project_root'],
            'sample_data', 'LongIslandSoundMap.BNA')

        # the land-water map
        model.map = gnome.map.MapFromBNA(
            map_file, refloat_halflife=6 * 3600)

        canvas = gnome.utilities.map_canvas.MapCanvas((800, 600))
        polygons = haz_files.ReadBNA(map_file, "PolygonSet")
        canvas.set_land(polygons)
        model.output_map = canvas

    # The client requested no cached images, so rewind and clear the cache.
    if request.POST.get('no_cache', False):
        model.runtime = _make_runtime()
        model.rewind()
        model.time_steps = []

    first_step = _get_time_step(request, model)

    if not first_step:
        return {}

    model.time_steps.append(first_step)
    data['time_step'] = first_step

    data['background_image'] = _get_model_image_url(
        request, model, 'background_map.png')
    data['map_bounds'] = model.map.map_bounds.tolist()

    return data


@view_config(route_name='run_model_until', renderer='gnome_json')
@util.json_require_model
def run_model_until(request, model):
    """
    Render a :class:`webgnome.forms.RunModelUntilForm` for the user's
    current model on GET and validate form input on POST.
    """
    form = RunModelUntilForm(request.POST)
    data = {}

    if request.method == 'POST' and form.validate():
        date = form.get_datetime()
        model.set_run_until(date)
        return {'run_until': date, 'form_html': None}

    context = {
        'form': form,
        'action_url': request.route_url('run_model_until')
    }

    data['form_html'] = render(
        'webgnome:templates/forms/run_model_until.mak', context)

    return data


@view_config(route_name='get_next_step', renderer='gnome_json')
@util.json_require_model
def get_next_step(request, model):
    """
    Generate the next step of a model run and return the result.
    """
    step = _get_time_step(request, model)

    if not step:
        raise HTTPNotFound

    model.time_steps.append(step)

    return {
        'time_step': step
    }


@view_config(route_name='get_tree', renderer='gnome_json')
@util.json_require_model
def get_tree(request, model):
    """
    Return a JSON representation of the current state of the model, to be used
    to create a tree view of the model in the JavaScript application.
    """
    return NavigationTree(request, model).render()


