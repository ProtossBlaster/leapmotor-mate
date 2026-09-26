"""Bounded cloud image composition, independent of the legacy SDK.

Asset names/order follow the examined app's CarPictureConstant/PictureManager.
Absent optional model layers are ignored; a body image is mandatory.
Pillow is imported lazily so headless API users do not require it.
"""
import io
import re
import zipfile


def build_layer_list(status):
    doors = status.doors
    # Far-side panels and tailgate must be occluded by the body. Drawing
    # their interior faces last makes a closed vehicle appear dismantled.
    layers = []
    for name, field in (
        ('rightfront', 'rbcm_driver_door_status'),
        ('rightbehind', 'rbcm_right_rear_door_status'),
        ('tailgate', 'bbcm_back_door_status'),
    ):
        layers.append('carpic_' + name + ('_open' if getattr(doors, field, 0) == 1 else '_close'))
    layers.append('carpic_body')
    for name, field in (
        ('leftfront', 'lbcm_driver_door_status'),
        ('leftbehind', 'lbcm_left_rear_door_status'),
    ):
        layers.append('carpic_' + name + ('_open' if getattr(doors, field, 0) == 1 else '_close'))
    layers.append('carpic_charge_open' if status.is_plugged else 'carpic_charge_close')
    layers.append('carpic_hood_close')
    for name, field in (('leftfront', 'left_front_window_percent'),
                        ('leftbehind', 'left_rear_window_percent')):
        if getattr(status.windows, field, 0) == 0:
            layers.append('carpic_' + name + '_window_close')
    if status.is_plugged:
        layers.append('carpic_charge1')
    return layers


class CarImagePackage:
    def __init__(self, images):
        self._images = images

    @classmethod
    def from_zip(cls, data):
        from PIL import Image
        if not isinstance(data, bytes) or len(data) > 32 * 1024 * 1024:
            raise ValueError('Invalid image archive size')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(m.file_size for m in members) > 128 * 1024 * 1024:
                raise ValueError('Image archive exceeds limits')
            names = [m.filename for m in members]
            prefix = next((f'android/{density}/' for density in
                           ('xxxhdpi', 'xxhdpi', 'xhdpi', 'hdpi', 'mdpi')
                           if f'android/{density}/carpic_body.png' in names), None)
            if prefix is None:
                raise ValueError('Missing vehicle body image')
            images = {}
            pixels = 0
            for member in members:
                if not member.filename.startswith(prefix):
                    continue
                name = member.filename[len(prefix):]
                if not re.fullmatch(r'carpic_[a-z0-9_]+\.png', name):
                    continue
                if name in images:
                    raise ValueError('Duplicate vehicle layer')
                with Image.open(io.BytesIO(archive.read(member))) as image:
                    if image.format != 'PNG' or max(image.size) > 4096:
                        raise ValueError('Invalid vehicle layer')
                    pixels += image.width * image.height
                    if pixels > 32_000_000:
                        raise ValueError('Decoded image budget exceeded')
                    images[name] = image.convert('RGBA')
        return cls(images)

    def _composite_layers(self, layers):
        from PIL import Image
        body = self._images['carpic_body.png']
        layers = list(layers)
        closed_panels = {
            'carpic_body', 'carpic_leftfront_close', 'carpic_leftbehind_close',
            'carpic_rightfront_close', 'carpic_rightbehind_close',
            'carpic_tailgate_close', 'carpic_hood_close',
            'carpic_leftfront_window_close', 'carpic_leftbehind_window_close',
        }
        names = {name.removesuffix('.png') for name in layers}
        closed_image = self._images.get('carpic_for_tripsum.png')
        # The panel sprites are articulated views, not an exact closed-body
        # replacement. Use the cloud's complete closed image when appropriate;
        # charging overlays can still be composited above it.
        if closed_image is not None and closed_panels <= names:
            result = closed_image.resize(body.size, Image.Resampling.LANCZOS)
            layers = [name for name in layers if name.removesuffix('.png') not in closed_panels]
        else:
            result = Image.new('RGBA', body.size)
        for name in layers:
            layer = self._images.get(name if name.endswith('.png') else name + '.png')
            if layer is None:
                continue
            if layer.size != body.size:
                raise ValueError('Mismatched vehicle layer dimensions')
            result = Image.alpha_composite(result, layer)
        return result

    def compose(self, status):
        output = io.BytesIO()
        self._composite_layers(build_layer_list(status)).save(output, format='PNG')
        return output.getvalue()
