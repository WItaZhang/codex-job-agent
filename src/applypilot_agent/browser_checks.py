"""Fixed, read-only DOM inspection scripts used by the browser executor.

All dynamic selectors are passed as data to these scripts. None of the plan's
contents is interpolated into or evaluated as JavaScript.
"""

INSPECT_FIELDS = r"""() => {
    const visible = e => !!(e.getClientRects().length) &&
        getComputedStyle(e).visibility !== 'hidden';
    const selector = e => {
        if (e.id) {
            const s = '#' + CSS.escape(e.id);
            if (document.querySelectorAll(s).length === 1) return s;
        }
        if (e.name) {
            const s = e.tagName.toLowerCase() + '[name=' + CSS.escape(e.name) + ']';
            if (document.querySelectorAll(s).length === 1) return s;
            if (['checkbox','radio'].includes(e.type) && e.hasAttribute('value')) {
                const choice = s + '[type=' + e.type + '][value=' + CSS.escape(e.value) + ']';
                if (document.querySelectorAll(choice).length === 1) return choice;
            }
        }
        return null;
    };
    return Array.from(document.querySelectorAll('input,textarea,select,[contenteditable=true],[role=combobox]'))
      .filter(e => visible(e) && !['hidden', 'submit', 'button', 'reset'].includes(e.type))
      .map(e => ({
          selector: selector(e), tag: e.tagName.toLowerCase(), name: e.name || '',
          type: e.type || e.getAttribute('role') || e.tagName.toLowerCase(),
          choice_value: ['radio','checkbox'].includes(e.type) ? e.value : null,
          label: (Array.from(e.labels || []).map(l => l.innerText).join(' ') ||
                  e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim(),
          required: !!e.required || e.getAttribute('aria-required') === 'true',
          disabled: !!e.disabled, multiple: !!e.multiple,
          options: e.tagName === 'SELECT' ? Array.from(e.options).map(o =>
              ({value: o.value, label: o.label, disabled: o.disabled})) : []
      }));
}"""

FIELD_METADATA = """e => ({
    tag: e.tagName.toLowerCase(), type: e.type || '', disabled: !!e.disabled,
    multiple: !!e.multiple, readOnly: !!e.readOnly
})"""

CHECK_REQUIRED = r"""selectors => {
    const selected = selectors.map(s => document.querySelector(s));
    const duplicate = selected.some((e, i) => selected.indexOf(e) !== i);
    const radioCovered = e => e.type === 'radio' && e.name && selected.some(s =>
        s && s.type === 'radio' && s.name === e.name && s.form === e.form);
    const conflictingRadios = selected.some((e, i) => e && e.type === 'radio' && e.name &&
        selected.slice(i + 1).some(s => s && s.type === 'radio' && s.name === e.name && s.form === e.form));
    const required = Array.from(document.querySelectorAll(
        'input[required],textarea[required],select[required],[aria-required=true]'))
        .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.disabled);
    const controls = Array.from(document.querySelectorAll('input,textarea,select'))
        .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' &&
                     !e.disabled && !['hidden','submit','button','reset'].includes(e.type));
    const unplanned = controls.filter(e => !selected.includes(e) && !radioCovered(e) &&
        (['checkbox','radio'].includes(e.type) ? e.checked :
         e.type === 'file' ? e.files.length : e.value !== ''));
    return {duplicate, conflictingRadios,
        unplanned: unplanned.map(e => ({id: e.id, name: e.name || '', type: e.type})),
        missing: required.filter(e => !selected.includes(e) && !radioCovered(e)).map(e => ({
        id: e.id, name: e.name || '', tag: e.tagName.toLowerCase(), type: e.type || ''
    }))};
}"""

FILE_SELECTION = "e => Array.from(e.files || []).map(f => ({name: f.name, size: f.size}))"

FORM_VALIDITY = "e => !('validity' in e) || e.validity.valid"

RECEIPT_CONTAINER = """e => !e.closest(
    'button,input,textarea,select,a,label,[role=button],[role=textbox],[role=combobox]'
) && !['HTML','BODY','FORM'].includes(e.tagName)"""
