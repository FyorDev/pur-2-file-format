#pragma author Fyor
#pragma description PureRef 2.0 / 2.1 .pur: a SQLite database behind a displaced header

// A 2.x .pur is an ordinary SQLite database whose first H bytes were replaced by
// a PureRef header and appended to the end instead, so the database is
// `file[N:] + file[H:N]` for a database length of N. Carving from the
// `SQLite format 3` signature recovers only the displaced prefix, which is why
// this pattern reassembles both halves into a section and hands the result to
// ImHex as a "scene.sqlite" virtual file: open it from the Virtual Filesystem
// tab, or in any SQLite browser, and the scene is seven ordinary tables.
//
// FORMAT.md in this repository documents every table, column and serialized
// value, and schema.sql is the schema this pattern's field names follow.
//
// The 1.x binary format is a different thing entirely; a pattern covering both
// generations lives in https://github.com/FyorDev/PureRef-format (pur.hexpat).

#pragma endian big

#pragma magic [ 00 00 00 06 00 32 00 2E 00 30 ] @ 0x00
#pragma magic [ 00 00 00 06 00 32 00 2E 00 31 ] @ 0x00

import std.core;
import std.io;
import std.mem;
import std.string;
import std.sys;

#ifdef __IMHEX__
    // Only ImHex itself can offer the reconstructed database as a virtual file.
    import hex.core;
#endif

// --- Qt primitives ----------------------------------------------------------

namespace qt {

    /**
        A QString: a byte count, then UTF-16BE code units. `0xffffffff` is null.
    */
    struct String {
        u32 length;
        if (length != 0xFFFFFFFF)
            char16 value[length / 2];
    } [[sealed, format("qt::format_string"), transform("qt::format_string")]];

    fn format_string(ref auto string) {
        if (string.length == 0xFFFFFFFF)
            return "<null>";
        if (string.length == 0)
            return "";
        return string.value;
    };
}

// --- The header -------------------------------------------------------------

/**
    The preview PureRef shows in a file dialog, as JPEG or PNG bytes. The same
    image is stored again in `metadata.thumbnail` inside the database. The 2.0
    layout has no such field: it ends at the checksum.
*/
struct Thumbnail {
    u32 length [[comment("0xffffffff would be a null QByteArray; PureRef writes real bytes")]];
    std::mem::Bytes<length == 0xFFFFFFFF ? 0 : length> value [[color("7F5FAF")]];
}
#ifdef __IMHEX__
    [[hex::visualize("image", value)]]
#endif
;

struct Header {
    qt::String format_version [[comment("2.0 or 2.1; 2.0.3 already writes 2.1")]];
    std::assert(format_version == "2.0" || format_version == "2.1",
                std::format("Not a PureRef 2.x file: version string {}", format_version));
    u32 reserved [[comment("zero in every observed file")]];
    u64 database_size [[comment("N: the database's own length")]];
    qt::String application_version;
    qt::String checksum
        [[comment("MD5 of everything after this field: the thumbnail and both halves")]];
    if (format_version != "2.0")
        Thumbnail thumbnail;
};

// --- The database -----------------------------------------------------------

u32 pageSize = 4096;          // taken from the reconstructed database header

struct SqliteHeader {
    char magic[16] [[comment("SQLite format 3")]];
    u16 page_size [[comment("1 means 65536; PureRef writes 4096")]];
    u8 write_version;
    u8 read_version;
    u8 reserved_space;
    u8 max_payload_fraction;
    u8 min_payload_fraction;
    u8 leaf_payload_fraction;
    u32 file_change_counter;
    u32 page_count;
    u32 first_freelist_page;
    u32 freelist_page_count;
    u32 schema_cookie;
    u32 schema_format;
    u32 default_page_cache_size;
    u32 largest_root_btree_page [[comment("non-zero: PureRef writes auto_vacuum=FULL")]];
    u32 text_encoding [[comment("1 = UTF-8")]];
    u32 user_version
        [[comment("the compatibility gate: 200101. A lower value is migrated, a higher one refused")]];
    u32 incremental_vacuum;
    u32 application_id [[comment("940753918 = 0x3812C3FE")]];
    u8 reserved[20];
    u32 version_valid_for;
    u32 sqlite_version_number;
};

/**
    A b-tree page header, so the database can be walked page by page. Reading
    cells out of these by hand is not the intended route -- query the virtual
    file instead -- but the map shows where a damaged file went wrong.
*/
struct PageHeader {
    u8 type [[comment("2 interior index, 5 interior table, 10 leaf index, 13 leaf table")]];
    u16 first_freeblock;
    u16 cell_count;
    u16 cell_content_start [[comment("0 means 65536")]];
    u8 fragmented_free_bytes;
    if (type == 2 || type == 5)
        u32 right_child;
    u16 cell_pointers[cell_count];
};

struct Page {
    u128 start = $;
    // Page 1 carries the 100-byte file header before its b-tree header.
    if (std::core::array_index() == 0)
        SqliteHeader file_header;
    if (std::mem::read_unsigned($, 1) != 0)
        PageHeader page;
    else
        u8 unused_page [[comment("a freelist or overflow page")]];
    padding[pageSize - ($ - start)];
};

struct PurFile {
    Header header;
    u128 header_size = $;
    std::assert(header.database_size >= header_size && header.database_size <= std::mem::size(),
                "Header length and database size do not add up; the file is truncated");

    // The two halves, shown where they actually sit in the file.
    u8 moved_body[std::mem::size() - header.database_size] @ header.database_size
        [[comment("database[H:N], the part that was not displaced")]];
    u8 moved_prefix[header.database_size - header_size] @ header_size
        [[comment("database[0:H], moved here to make room for the header")]];

    std::mem::Section database = std::mem::create_section("database");
    std::mem::copy_value_to_section(moved_body, database, 0);
    std::mem::copy_value_to_section(moved_prefix, database, sizeof(moved_body));

    SqliteHeader sqlite @ 0x00 in database;
    std::assert(std::string::starts_with(sqlite.magic, "SQLite format 3"),
                "The reconstructed prefix is not a SQLite database");
    pageSize = sqlite.page_size == 1 ? 65536 : sqlite.page_size;

    // A page map, for files small enough that mapping them is cheap.
    if (sqlite.page_count <= 512)
        Page pages[sqlite.page_count] @ 0x00 in database;
    u8 bytes[std::mem::get_section_size(database)] @ 0x00 in database [[hidden]];

    #ifdef __IMHEX__
        hex::core::add_virtual_file("scene.sqlite", bytes);
    #endif
};

/**
    A serialized cell. The `transform`, `sort_order`, `image_transform`,
    `image_bounds`, `fixed_size`, `scene_rect`, `view_transform` and `strokes`
    columns each hold one of these: a QDataStream QVariant, which is a type id, a
    null flag, a registered type name for custom types, and then the value.

    Place it by hand -- `Variant v @ 0x123;` -- on a payload you extracted. It
    does not fit a raw SQLite page: those cells have storage class TEXT, so every
    payload byte above 0x7f was re-encoded as UTF-8 and has to be mapped back
    through Latin-1 first, which is what `pureref2.binary()` does.
*/
struct Variant {
    u32 type_id [[comment("20 QRectF, 22 QSizeF, 80 QTransform, 1024 a named custom type")]];
    u8 is_null;
    if (type_id == 1024)
        std::string::NullString type_name
            [[comment("QPainterPath, BigRational or QList<GraphicsDrawItem::Stroke>")]];
    if (type_id == 20)
        double rect[4] [[comment("x, y, width, height")]];
    else if (type_id == 22)
        double size[2] [[comment("width, height; (-1,-1) means automatic")]];
    else if (type_id == 80)
        double transform[9] [[comment("m11 m12 m13 m21 m22 m23 m31 m32 m33")]];
};

PurFile pur @ 0x00;
