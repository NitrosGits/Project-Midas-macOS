/* PORT: the node and record pointers are PTR32 words (bondtypes.h). A 64-bit
 * build can't encode them in a static initializer, so PORT_SPTR leaves 0 there
 * and PORT_STATIC_PTR32 has portWindowInit fill them in at boot (identity in
 * the 32-bit builds). */
//D:8003C580
ModelNode player_gait_hdr = {1, PORT_SPTR(&player_gait_obj), 0, 0, 0, PORT_SPTR(&player_gait_pos_hdr_1)};
PORT_STATIC_PTR32(player_gait_hdr.Data, &player_gait_obj);
PORT_STATIC_PTR32(player_gait_hdr.Child, &player_gait_pos_hdr_1);
//D:8003C598
ModelNode player_gait_pos_hdr_1 = {2, PORT_SPTR(&player_gait_pos_1), PORT_SPTR(&player_gait_hdr), 0, 0, PORT_SPTR(&player_gait_pos_hdr_2)};
PORT_STATIC_PTR32(player_gait_pos_hdr_1.Data, &player_gait_pos_1);
PORT_STATIC_PTR32(player_gait_pos_hdr_1.Parent, &player_gait_hdr);
PORT_STATIC_PTR32(player_gait_pos_hdr_1.Child, &player_gait_pos_hdr_2);
//D:8003C5B0
ModelNode player_gait_pos_hdr_2 = {2, PORT_SPTR(&player_gait_pos_2), PORT_SPTR(&player_gait_pos_hdr_1), 0, 0, PORT_SPTR(&player_gait_pos_hdr_3)};
PORT_STATIC_PTR32(player_gait_pos_hdr_2.Data, &player_gait_pos_2);
PORT_STATIC_PTR32(player_gait_pos_hdr_2.Parent, &player_gait_pos_hdr_1);
PORT_STATIC_PTR32(player_gait_pos_hdr_2.Child, &player_gait_pos_hdr_3);
//D:8003C5C8
ModelNode player_gait_pos_hdr_3 = {2, PORT_SPTR(&player_gait_pos_3), PORT_SPTR(&player_gait_pos_hdr_2), 0, 0, 0};
PORT_STATIC_PTR32(player_gait_pos_hdr_3.Data, &player_gait_pos_3);
PORT_STATIC_PTR32(player_gait_pos_hdr_3.Parent, &player_gait_pos_hdr_2);
//D:8003C5E0
ModelRoData_HeaderRecord player_gait_obj = {0, 1, PORT_SPTR(&player_gait_pos_hdr_1), 0, 0, 0};
PORT_STATIC_PTR32(player_gait_obj.FirstGroup, &player_gait_pos_hdr_1);
//D:8003C5F0
ModelRoData_GroupRecord player_gait_pos_1 = {{0.0, 0.0, 0.0}, 0x0001, 0x0002, 0xFFFF, 0xFFFF, PORT_SPTR(&player_gait_pos_hdr_2), 0};
PORT_STATIC_PTR32(player_gait_pos_1.ChildGroup, &player_gait_pos_hdr_2);
//D:8003C60C
ModelRoData_GroupRecord player_gait_pos_2 = {{1.177982, 41.14437, 0.0}, 0x0002, 0x0003, 0xFFFF, 0xFFFF, PORT_SPTR(&player_gait_pos_hdr_3), 0};
PORT_STATIC_PTR32(player_gait_pos_2.ChildGroup, &player_gait_pos_hdr_3);
//D:8003C628
ModelRoData_GroupRecord player_gait_pos_3 = {{-2.576027, 480.42902, 0.0}, 0x0003, 0x0000, 0xFFFF, 0xFFFF, 0, 0};

//FIXME File split likely
//D:8003C644
s32 PAD_8003C644 = 0;
//D:8003C648
s32 PAD_8003C648 = 0;
//D:8003C64C
s32 PAD_8003C64C = 0;
