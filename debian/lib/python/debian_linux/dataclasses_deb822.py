from __future__ import annotations

import re
from dataclasses import (
    Field,
    fields,
    MISSING,
)
from typing import (
    Any,
    Callable,
    IO,
    Iterable,
    Optional,
    overload,
    TypeVar,
    TYPE_CHECKING,
)

if TYPE_CHECKING:
    from dataclasses import _DataclassT

_T = TypeVar('_T')


# It is not actually specified if you may subclass dataclasses.Field, but all
# the checks are correct to allow it.
class Deb822Field(Field[_T]):
    deb822_key: str
    deb822_load: Optional[Callable[[str], _T]]
    deb822_dump: Optional[Callable[[_T], str]]

    def __init__(
        self, *,
        deb822_key: str,
        deb822_load: Optional[Callable[[str], _T]],
        deb822_dump: Optional[Callable[[_T], str]],
        default,
        default_factory,
    ) -> None:
        super().__init__(default, default_factory, True, True, None, True, {}, True)
        self.deb822_key = deb822_key
        self.deb822_load = deb822_load
        self.deb822_dump = deb822_dump


# The return type _T is technically wrong, but it allows checking if during
# runtime we get the correct type.
@overload
def field_deb822(
    deb822_key: str,
    /, *,
    deb822_load: Optional[Callable[[str], _T]] = None,
    deb822_dump: Optional[Callable[[_T], str]] = str,
    default: _T,
) -> _T: ...


@overload
def field_deb822(
    deb822_key: str,
    /, *,
    deb822_load: Optional[Callable[[str], _T]] = None,
    deb822_dump: Optional[Callable[[_T], str]] = str,
    default_factory: Callable[[], _T],
) -> _T: ...


@overload
def field_deb822(
    deb822_key: str,
    /, *,
    deb822_load: Optional[Callable[[str], _T]] = None,
    deb822_dump: Optional[Callable[[_T], str]] = str,
) -> _T: ...


def field_deb822(
    deb822_key: str,
    /, *,
    deb822_load: Optional[Callable[[str], _T]] = None,
    deb822_dump: Optional[Callable[[_T], str]] = str,
    default: Any = MISSING,
    default_factory: Any = MISSING,
) -> Any:
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError('cannot specify both default and default_factory')
    return Deb822Field(
        default=default,
        default_factory=default_factory,
        deb822_key=deb822_key,
        deb822_load=deb822_load,
        deb822_dump=deb822_dump,
    )


class Deb822DecodeError(ValueError):
    pass


_DEB822_LINE_RE = re.compile(r'''
    ^
    (
        [ \t](?P<cont>.*)
        |
        Meta-(?P<metakey>[^: \t\n\r\f\v]+)\s*:\s*(?P<metavalue>.*)
        |
        (?P<key>[^: \t\n\r\f\v]+)\s*:\s*(?P<value>.*)
    )
    $
''', re.VERBOSE)


def read_deb822(
    cls: type[_DataclassT],
    file: IO[str],
    /
) -> Iterable[_DataclassT]:
    global _DEB822_LINE_RE

    deb822_fields: dict[str, Deb822Field] = {}
    for i in fields(cls):
        if i.init and isinstance(i, Deb822Field):
            deb822_fields[i.deb822_key] = i

    class State:
        data: dict[Field, str]
        meta: dict[str, str]
        current: Optional[Field]

        def __init__(self) -> None:
            self.reset()

        def reset(self) -> None:
            self.data = {}
            self.meta = {}
            self.current = None

        def __call__(self) -> _DataclassT:
            r: dict[str, Any] = {
                'meta': self.meta,
            }
            for field, value in self.data.items():
                field_factory: Optional[Callable[[str], Any]] = None
                if isinstance(field, Deb822Field) and field.deb822_load:
                    field_factory = field.deb822_load
                elif isinstance(field.default_factory, type):
                    field_factory = field.default_factory
                elif field.type in ('str', 'Optional[str]'):
                    field_factory = str
                else:
                    raise RuntimeError(f'Unable to parse type {field.type}')

                if field_factory is not None:
                    r[field.name] = field_factory(value)
            return cls(**r)

    state = State()

    for linenr, line in enumerate(file):
        # Strip comments rather than trying to preserve them
        if line[0] == '#':
            continue

        line = line.strip('\n')
        # Empty line, end of record
        if not line:
            if state.data:
                yield state()
            state.reset()
            continue

        m = _DEB822_LINE_RE.match(line)
        if not m:
            raise Deb822DecodeError(
                f'Not a header, not a continuation at line {linenr + 1}')
        elif m.group('cont'):
            if not state.current:
                raise Deb822DecodeError(
                    f'Continuation line seen before first header at line {linenr + 1}')
            state.data[state.current] += '\n' + line
        elif deb822_key := m.group('key'):
            if deb822_field := deb822_fields.get(deb822_key):
                state.current = deb822_field
                state.data[state.current] = m.group('value')
            else:
                raise Deb822DecodeError(
                    f'Unknown field "{deb822_key}" at line {linenr + 1}')
        elif metakey := m.group('metakey'):
            state.meta[metakey.lower()] = m.group('metavalue')
            # Meta keys don't support continuation right now
            state.current = None

    if state.data:
        yield state()


def write_deb822(
    objs: Iterable[_DataclassT],
    file: IO[str],
    /
) -> None:
    for obj in objs:
        for field in fields(obj):
            value = getattr(obj, field.name, None)
            if value and isinstance(field, Deb822Field) and field.deb822_dump is not None:
                file.write(f'{field.deb822_key}: {value}\n')
        file.write('\n')
