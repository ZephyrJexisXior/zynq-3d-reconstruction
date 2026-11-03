module dual_view_pl #(parameter integer IMAGE_WIDTH=2592, IMAGE_HEIGHT=1944)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF CAM:DDR:M_AXIS:S_AXI, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 CAM TDATA" *)
    input wire [23:0] cam_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 CAM TVALID" *)
    input wire  cam_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 CAM TREADY" *)
    output wire  cam_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 CAM TUSER" *)
    input wire  cam_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 CAM TLAST" *)
    input wire  cam_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 DDR TDATA" *)
    input wire [23:0] ddr_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 DDR TVALID" *)
    input wire  ddr_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 DDR TREADY" *)
    output wire  ddr_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 DDR TUSER" *)
    input wire  ddr_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 DDR TLAST" *)
    input wire  ddr_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output wire [23:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire  m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input wire  m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TUSER" *)
    output wire  m_axis_tuser,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire  m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 8, READ_WRITE_MODE READ_WRITE, HAS_WSTRB 1, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1" *)
    input wire [7:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire  s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire  s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire  s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire  s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire  s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire  s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [7:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire  s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire  s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire  s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire  s_axi_rready,
    output wire pipe_aresetn,
    input wire [23:0] mon_data,
    input wire mon_valid, mon_ready, mon_user, mon_last
);

// 0x00 run[0], DDR source[1]. Switch source only while disabled and drained.
// 0x04 command: arm front[0], arm upper[1], match[2], invalidate[3].
// 0x08 status: capture busy[0], match busy[1], bank valid[3:2], done[4], fault[5], output valid[6].
// 0x0C version; 0x10/14 features; 0x18 matches; 0x1C generation;
// 0x20 result index; 0x24 front xy; 0x28 upper xy; 0x2C valid[31], distance[8:0].
// Features are FAST9 + fixed, unrotated BRIEF256 on a 1/4 sampled image.
// This is a correspondence precheck, not calibrated geometry or full ORB.
localparam SW=IMAGE_WIDTH/4, SH=IMAGE_HEIGHT/4;
localparam GX=(SW+31)/32, GY=(SH+31)/32, N=GX*GY;
localparam IW=(N<2)?1:$clog2(N);
reg run=1, source=0;
reg wait_sof=1;
assign pipe_aresetn=aresetn && run;
reg [23:0] out_data;
reg out_valid=0,out_user=0,out_last=0;
wire ready_out=!out_valid || m_axis_tready;
wire [23:0] selected_data=source?ddr_tdata:cam_tdata;
wire selected_valid=source?ddr_tvalid:cam_tvalid;
assign cam_tready=(!source && run)?ready_out:1'b1;
assign ddr_tready=(source && run)?ready_out:1'b0;
assign m_axis_tdata=out_data;
assign m_axis_tvalid=out_valid;
assign m_axis_tuser=out_user;
assign m_axis_tlast=out_last;
reg aw_pending=0,w_pending=0,bvalid=0,rvalid=0;
reg [7:0] awaddr;
reg [31:0] wdata,rdata;
reg [3:0] wstrb;
reg [1:0] bresp;
assign s_axi_awready=!aw_pending && !bvalid;
assign s_axi_wready=!w_pending && !bvalid;
assign s_axi_bvalid=bvalid;
assign s_axi_bresp=bresp;
assign s_axi_arready=!rvalid;
assign s_axi_rvalid=rvalid;
assign s_axi_rdata=rdata;
assign s_axi_rresp=0;
wire write_fire=aw_pending && w_pending && !bvalid;
reg arm=0,arm_bank=0,match_start=0,invalidate=0;
reg fault=0;
wire cap_busy;
wire match_busy;
reg [15:0] result_index=0;
reg [31:0] generation=0;
reg [31:0] result0[N-1:0],result1[N-1:0],result_status[N-1:0];
reg [31:0] read0,read1,read_status;
always @(posedge aclk) begin
    if(result_index<N) begin
        read0<=result0[result_index]; read1<=result1[result_index];
        read_status<=result_status[result_index];
    end else begin read0<=0;read1<=0;read_status<=0;end
end
reg [1:0] bank_valid=0;
reg [15:0] features0=0,features1=0,match_count=0;
reg match_done=0;
always @(posedge aclk) begin
    if(!aresetn) begin
        run<=1;source<=0;wait_sof<=1;out_valid<=0;aw_pending<=0;w_pending<=0;
        bvalid<=0;rvalid<=0;arm<=0;match_start<=0;invalidate<=0;
        fault<=0;generation<=0;result_index<=0;
    end else begin
        if(!run) begin
            if(ready_out) out_valid<=0;
            wait_sof<=1;
        end
        else if(ready_out) begin
            out_valid<=selected_valid && (!wait_sof || (source?ddr_tuser:cam_tuser));
            if(selected_valid && (source?ddr_tuser:cam_tuser)) wait_sof<=0;
            out_data<=selected_data;out_user<=source?ddr_tuser:cam_tuser;
            out_last<=source?ddr_tlast:cam_tlast;
        end
        arm<=0;match_start<=0;invalidate<=0;
        if(s_axi_awready && s_axi_awvalid) begin aw_pending<=1;awaddr<=s_axi_awaddr;end
        if(s_axi_wready && s_axi_wvalid) begin w_pending<=1;wdata<=s_axi_wdata;wstrb<=s_axi_wstrb;end
        if(bvalid && s_axi_bready) bvalid<=0;
        if(write_fire) begin
            aw_pending<=0;w_pending<=0;bvalid<=1;bresp<=0;
            case(awaddr & 8'hFC)
            0: if(wstrb[0]) begin
                if(cap_busy || match_busy || (wdata[1]!=source && (run || out_valid))) begin bresp<=2;fault<=1;end
                else begin run<=wdata[0];source<=wdata[1];end
            end
            4: if(wstrb[0]) begin
                if(wdata[3] && !match_busy) begin invalidate<=1;fault<=0;end
                else if(cap_busy || match_busy || arm || match_start ||
                   ((wdata[2:0] & (wdata[2:0]-1))!=0) ||
                   (wdata[2] && bank_valid!=3)) begin bresp<=2;fault<=1;end
                else begin
                    arm<=|wdata[1:0];arm_bank<=wdata[1];match_start<=wdata[2];
                    invalidate<=wdata[3];fault<=0;
                    if(|wdata[2:0]) generation<=generation+1;
                end
            end
            8'h20: begin
                if(wstrb[0]) result_index[7:0]<=wdata[7:0];
                if(wstrb[1]) result_index[15:8]<=wdata[15:8];
            end
            default: bresp<=2;
            endcase
        end
        if(rvalid && s_axi_rready) rvalid<=0;
        if(s_axi_arready && s_axi_arvalid) begin
            rvalid<=1;
            case(s_axi_araddr & 8'hFC)
            0:rdata<={30'b0,source,run};4:rdata<=0;
            8:rdata<={25'b0,out_valid,fault,match_done,bank_valid,match_busy,cap_busy};
            12:rdata<=32'h44565031;
            16:rdata<={16'b0,features0};20:rdata<={16'b0,features1};
            24:rdata<={16'b0,match_count};28:rdata<=generation;
            32:rdata<={16'b0,result_index};36:rdata<=match_done?read0:0;
            40:rdata<=match_done?read1:0;44:rdata<=match_done?read_status:0;
            48:rdata<=N;52:rdata<={IMAGE_HEIGHT[15:0],IMAGE_WIDTH[15:0]};
            default:rdata<=0;
            endcase
        end
    end
end
// Stores are cleared sequentially; an unfilled cell can never expose old features.
(* ram_style="block" *) reg [287:0] bank0[N-1:0],bank1[N-1:0];
reg [7:0] score0[N-1:0],score1[N-1:0];
reg [1:0] cap_state=0;
reg cap_bank=0;
reg [15:0] clear_index=0;
reg [11:0] x=0,y=0;
reg [31:0] pixels=0;
reg shape_bad=0;
assign cap_busy=cap_state!=0 || arm;
wire fire=mon_valid && mon_ready;
wire [11:0] cx=mon_user?12'd0:x,cy=mon_user?12'd0:y;
wire collecting=(cap_state==3) || (cap_state==2 && mon_user);
wire sample_fire=fire && collecting && cx[1:0]==0 && cy[1:0]==0 && cx<IMAGE_WIDTH && cy<IMAGE_HEIGHT;
wire [9:0] sx=cx>>2,sy=cy>>2;
wire [9:0] gray_sum={2'b0,mon_data[23:16]}+{1'b0,mon_data[15:8],1'b0}+{2'b0,mon_data[7:0]};
reg [7:0] line0[SW-1:0],line1[SW-1:0],line2[SW-1:0],line3[SW-1:0];
reg [7:0] line4[SW-1:0],line5[SW-1:0],line6[SW-1:0],line7[SW-1:0];
reg [7:0] taps[8:0];
reg sample_pending=0,eval_pending=0;
reg [9:0] sample_x,sample_y,eval_x,eval_y;
reg [7:0] window[8:0][8:0];
integer i,j;
always @(posedge aclk) begin
    if(!aresetn || arm || invalidate) begin sample_pending<=0;eval_pending<=0;end
    else begin
        sample_pending<=sample_fire;
        eval_pending<=sample_pending;
        if(sample_fire) begin
            taps[0]<=line7[sx];taps[1]<=line6[sx];taps[2]<=line5[sx];taps[3]<=line4[sx];
            taps[4]<=line3[sx];taps[5]<=line2[sx];taps[6]<=line1[sx];taps[7]<=line0[sx];taps[8]<=gray_sum[9:2];
            line7[sx]<=line6[sx];line6[sx]<=line5[sx];line5[sx]<=line4[sx];line4[sx]<=line3[sx];
            line3[sx]<=line2[sx];line2[sx]<=line1[sx];line1[sx]<=line0[sx];line0[sx]<=gray_sum[9:2];
            sample_x<=sx;sample_y<=sy;
        end
        if(sample_pending) begin
            for(i=0;i<9;i=i+1) begin
                for(j=0;j<8;j=j+1) window[i][j]<=window[i][j+1];
                window[i][8]<=taps[i];
            end
            eval_x<=sample_x;eval_y<=sample_y;
        end
    end
end
reg [7:0] circle[15:0];
integer k,diff;
reg [15:0] bright,dark;
reg [31:0] bright_twice,dark_twice;
reg [7:0] abs_diff[15:0];
reg [8:0] score2[7:0];
reg [9:0] score4[3:0];
reg [10:0] score8[1:0];
reg [11:0] score16;
reg [7:0] corner_score;
reg corner;
reg [255:0] brief;
always @* begin
    circle[0]=window[1][4];circle[1]=window[1][5];circle[2]=window[2][6];circle[3]=window[3][7];
    circle[4]=window[4][7];circle[5]=window[5][7];circle[6]=window[6][6];circle[7]=window[7][5];
    circle[8]=window[7][4];circle[9]=window[7][3];circle[10]=window[6][2];circle[11]=window[5][1];
    circle[12]=window[4][1];circle[13]=window[3][1];circle[14]=window[2][2];circle[15]=window[1][3];
    for(k=0;k<16;k=k+1) begin
        diff={1'b0,circle[k]}-{1'b0,window[4][4]};
        bright[k]=diff>16;dark[k]=diff< -16;
        abs_diff[k]=diff<0?-diff:diff;
    end
    bright_twice={bright,bright};dark_twice={dark,dark};corner=0;
    for(k=0;k<16;k=k+1) corner=corner || (&bright_twice[k+:9]) || (&dark_twice[k+:9]);
    for(k=0;k<8;k=k+1) score2[k]={1'b0,abs_diff[k*2]}+{1'b0,abs_diff[k*2+1]};
    for(k=0;k<4;k=k+1) score4[k]={1'b0,score2[k*2]}+{1'b0,score2[k*2+1]};
    for(k=0;k<2;k=k+1) score8[k]={1'b0,score4[k*2]}+{1'b0,score4[k*2+1]};
    score16={1'b0,score8[0]}+{1'b0,score8[1]};corner_score=score16[11:4];
    brief=0;
    brief[0]=window[0][4]<window[8][4];
    brief[1]=window[6][0]<window[3][5];
    brief[2]=window[5][2]<window[2][6];
    brief[3]=window[3][8]<window[5][8];
    brief[4]=window[0][0]<window[2][6];
    brief[5]=window[1][3]<window[3][1];
    brief[6]=window[5][7]<window[6][2];
    brief[7]=window[4][5]<window[2][5];
    brief[8]=window[2][3]<window[8][0];
    brief[9]=window[7][4]<window[2][6];
    brief[10]=window[3][3]<window[4][0];
    brief[11]=window[1][6]<window[6][3];
    brief[12]=window[3][7]<window[0][1];
    brief[13]=window[1][3]<window[1][7];
    brief[14]=window[8][2]<window[4][0];
    brief[15]=window[2][1]<window[7][4];
    brief[16]=window[0][3]<window[7][4];
    brief[17]=window[2][5]<window[4][8];
    brief[18]=window[1][5]<window[0][1];
    brief[19]=window[1][5]<window[7][6];
    brief[20]=window[1][7]<window[1][5];
    brief[21]=window[5][1]<window[1][6];
    brief[22]=window[8][7]<window[1][7];
    brief[23]=window[1][4]<window[3][0];
    brief[24]=window[5][8]<window[6][1];
    brief[25]=window[8][6]<window[3][6];
    brief[26]=window[7][3]<window[8][2];
    brief[27]=window[6][8]<window[7][0];
    brief[28]=window[3][3]<window[5][8];
    brief[29]=window[3][2]<window[6][2];
    brief[30]=window[6][4]<window[2][2];
    brief[31]=window[8][4]<window[6][8];
    brief[32]=window[2][2]<window[5][3];
    brief[33]=window[8][0]<window[5][1];
    brief[34]=window[4][4]<window[3][0];
    brief[35]=window[3][4]<window[1][5];
    brief[36]=window[0][7]<window[5][1];
    brief[37]=window[2][2]<window[6][1];
    brief[38]=window[3][4]<window[3][6];
    brief[39]=window[8][0]<window[0][4];
    brief[40]=window[1][5]<window[2][0];
    brief[41]=window[4][2]<window[8][5];
    brief[42]=window[1][4]<window[4][2];
    brief[43]=window[2][7]<window[4][5];
    brief[44]=window[1][1]<window[4][0];
    brief[45]=window[1][6]<window[3][2];
    brief[46]=window[5][2]<window[2][5];
    brief[47]=window[5][7]<window[4][8];
    brief[48]=window[8][0]<window[6][1];
    brief[49]=window[1][7]<window[0][6];
    brief[50]=window[3][7]<window[0][2];
    brief[51]=window[2][7]<window[7][1];
    brief[52]=window[5][4]<window[0][5];
    brief[53]=window[1][5]<window[7][5];
    brief[54]=window[4][1]<window[1][7];
    brief[55]=window[7][3]<window[3][2];
    brief[56]=window[2][0]<window[0][1];
    brief[57]=window[0][3]<window[8][4];
    brief[58]=window[5][3]<window[0][0];
    brief[59]=window[4][7]<window[8][4];
    brief[60]=window[5][5]<window[7][4];
    brief[61]=window[4][0]<window[3][7];
    brief[62]=window[1][4]<window[1][8];
    brief[63]=window[4][1]<window[1][8];
    brief[64]=window[3][4]<window[2][0];
    brief[65]=window[7][0]<window[8][2];
    brief[66]=window[4][5]<window[2][6];
    brief[67]=window[1][2]<window[6][0];
    brief[68]=window[0][8]<window[8][2];
    brief[69]=window[2][2]<window[3][5];
    brief[70]=window[5][5]<window[8][6];
    brief[71]=window[4][0]<window[0][7];
    brief[72]=window[8][6]<window[7][2];
    brief[73]=window[2][0]<window[1][2];
    brief[74]=window[4][8]<window[5][3];
    brief[75]=window[1][7]<window[2][6];
    brief[76]=window[2][7]<window[6][7];
    brief[77]=window[1][0]<window[0][7];
    brief[78]=window[3][2]<window[1][2];
    brief[79]=window[1][7]<window[7][3];
    brief[80]=window[3][0]<window[0][4];
    brief[81]=window[1][2]<window[4][0];
    brief[82]=window[6][5]<window[2][2];
    brief[83]=window[8][7]<window[4][2];
    brief[84]=window[1][6]<window[6][2];
    brief[85]=window[3][1]<window[2][3];
    brief[86]=window[8][2]<window[1][7];
    brief[87]=window[4][7]<window[7][0];
    brief[88]=window[7][7]<window[7][8];
    brief[89]=window[5][7]<window[4][1];
    brief[90]=window[6][0]<window[3][1];
    brief[91]=window[8][6]<window[5][4];
    brief[92]=window[8][4]<window[5][8];
    brief[93]=window[4][2]<window[1][8];
    brief[94]=window[5][0]<window[2][4];
    brief[95]=window[1][2]<window[2][1];
    brief[96]=window[0][4]<window[6][4];
    brief[97]=window[3][2]<window[1][0];
    brief[98]=window[7][0]<window[6][8];
    brief[99]=window[6][7]<window[2][0];
    brief[100]=window[4][8]<window[7][1];
    brief[101]=window[3][7]<window[4][8];
    brief[102]=window[6][7]<window[5][6];
    brief[103]=window[0][4]<window[4][2];
    brief[104]=window[8][5]<window[0][4];
    brief[105]=window[6][4]<window[7][3];
    brief[106]=window[1][5]<window[5][3];
    brief[107]=window[2][3]<window[4][1];
    brief[108]=window[0][8]<window[0][4];
    brief[109]=window[5][0]<window[0][7];
    brief[110]=window[6][7]<window[6][6];
    brief[111]=window[1][4]<window[2][3];
    brief[112]=window[0][7]<window[0][3];
    brief[113]=window[8][8]<window[7][6];
    brief[114]=window[4][5]<window[5][0];
    brief[115]=window[8][6]<window[3][2];
    brief[116]=window[8][7]<window[0][7];
    brief[117]=window[6][3]<window[4][3];
    brief[118]=window[8][5]<window[0][6];
    brief[119]=window[8][3]<window[0][7];
    brief[120]=window[7][6]<window[0][0];
    brief[121]=window[1][5]<window[4][4];
    brief[122]=window[0][8]<window[5][6];
    brief[123]=window[6][6]<window[8][6];
    brief[124]=window[0][1]<window[4][0];
    brief[125]=window[5][4]<window[3][6];
    brief[126]=window[1][5]<window[3][1];
    brief[127]=window[3][0]<window[7][8];
    brief[128]=window[3][4]<window[3][1];
    brief[129]=window[1][1]<window[0][8];
    brief[130]=window[2][6]<window[1][4];
    brief[131]=window[7][6]<window[0][1];
    brief[132]=window[8][8]<window[7][7];
    brief[133]=window[3][1]<window[1][0];
    brief[134]=window[6][0]<window[5][1];
    brief[135]=window[2][5]<window[5][3];
    brief[136]=window[0][4]<window[3][7];
    brief[137]=window[6][7]<window[7][4];
    brief[138]=window[6][2]<window[3][0];
    brief[139]=window[6][8]<window[6][0];
    brief[140]=window[7][7]<window[0][7];
    brief[141]=window[6][8]<window[3][4];
    brief[142]=window[6][2]<window[3][6];
    brief[143]=window[1][4]<window[6][5];
    brief[144]=window[1][5]<window[5][8];
    brief[145]=window[7][7]<window[3][5];
    brief[146]=window[4][0]<window[3][2];
    brief[147]=window[2][5]<window[3][4];
    brief[148]=window[4][8]<window[6][3];
    brief[149]=window[8][8]<window[8][7];
    brief[150]=window[8][1]<window[5][1];
    brief[151]=window[0][0]<window[1][2];
    brief[152]=window[3][1]<window[3][6];
    brief[153]=window[4][0]<window[5][0];
    brief[154]=window[2][8]<window[6][7];
    brief[155]=window[7][1]<window[2][2];
    brief[156]=window[6][2]<window[7][4];
    brief[157]=window[8][6]<window[0][6];
    brief[158]=window[1][3]<window[7][3];
    brief[159]=window[0][7]<window[2][7];
    brief[160]=window[0][8]<window[5][1];
    brief[161]=window[7][4]<window[5][8];
    brief[162]=window[2][6]<window[5][6];
    brief[163]=window[6][6]<window[5][6];
    brief[164]=window[1][1]<window[6][5];
    brief[165]=window[2][4]<window[3][2];
    brief[166]=window[1][1]<window[0][3];
    brief[167]=window[5][7]<window[0][0];
    brief[168]=window[8][7]<window[7][8];
    brief[169]=window[8][5]<window[4][0];
    brief[170]=window[6][5]<window[4][6];
    brief[171]=window[8][6]<window[1][7];
    brief[172]=window[3][0]<window[2][3];
    brief[173]=window[4][6]<window[8][4];
    brief[174]=window[7][0]<window[0][3];
    brief[175]=window[1][1]<window[3][2];
    brief[176]=window[3][8]<window[6][0];
    brief[177]=window[3][2]<window[2][7];
    brief[178]=window[7][6]<window[6][3];
    brief[179]=window[6][2]<window[6][5];
    brief[180]=window[0][5]<window[6][0];
    brief[181]=window[2][2]<window[1][2];
    brief[182]=window[0][8]<window[7][4];
    brief[183]=window[8][2]<window[6][5];
    brief[184]=window[2][5]<window[4][4];
    brief[185]=window[8][4]<window[0][8];
    brief[186]=window[7][1]<window[3][5];
    brief[187]=window[1][8]<window[4][0];
    brief[188]=window[8][3]<window[7][1];
    brief[189]=window[3][6]<window[4][5];
    brief[190]=window[1][3]<window[5][3];
    brief[191]=window[1][8]<window[3][3];
    brief[192]=window[2][5]<window[4][1];
    brief[193]=window[0][1]<window[6][1];
    brief[194]=window[8][7]<window[2][2];
    brief[195]=window[6][5]<window[4][2];
    brief[196]=window[7][2]<window[4][1];
    brief[197]=window[6][5]<window[3][5];
    brief[198]=window[0][3]<window[5][6];
    brief[199]=window[5][0]<window[1][0];
    brief[200]=window[6][2]<window[4][6];
    brief[201]=window[7][4]<window[4][7];
    brief[202]=window[6][7]<window[7][4];
    brief[203]=window[0][5]<window[0][0];
    brief[204]=window[5][3]<window[4][4];
    brief[205]=window[6][6]<window[3][1];
    brief[206]=window[7][1]<window[5][2];
    brief[207]=window[6][7]<window[0][2];
    brief[208]=window[2][8]<window[3][0];
    brief[209]=window[1][1]<window[5][5];
    brief[210]=window[6][3]<window[3][4];
    brief[211]=window[6][0]<window[8][4];
    brief[212]=window[7][8]<window[4][2];
    brief[213]=window[8][5]<window[2][0];
    brief[214]=window[6][0]<window[5][6];
    brief[215]=window[2][0]<window[5][4];
    brief[216]=window[0][7]<window[3][4];
    brief[217]=window[5][5]<window[3][3];
    brief[218]=window[7][8]<window[4][4];
    brief[219]=window[2][1]<window[3][1];
    brief[220]=window[8][1]<window[1][4];
    brief[221]=window[5][0]<window[3][1];
    brief[222]=window[6][8]<window[8][7];
    brief[223]=window[1][5]<window[2][6];
    brief[224]=window[7][8]<window[4][3];
    brief[225]=window[5][8]<window[7][8];
    brief[226]=window[2][6]<window[6][1];
    brief[227]=window[3][1]<window[0][6];
    brief[228]=window[1][2]<window[2][1];
    brief[229]=window[6][0]<window[5][6];
    brief[230]=window[3][7]<window[8][3];
    brief[231]=window[8][2]<window[5][0];
    brief[232]=window[6][4]<window[0][4];
    brief[233]=window[0][6]<window[4][6];
    brief[234]=window[3][4]<window[2][5];
    brief[235]=window[7][5]<window[7][2];
    brief[236]=window[1][5]<window[6][7];
    brief[237]=window[7][0]<window[8][1];
    brief[238]=window[3][4]<window[1][6];
    brief[239]=window[3][0]<window[1][3];
    brief[240]=window[7][2]<window[4][3];
    brief[241]=window[2][8]<window[7][1];
    brief[242]=window[4][4]<window[8][7];
    brief[243]=window[6][7]<window[7][7];
    brief[244]=window[0][4]<window[3][3];
    brief[245]=window[8][6]<window[2][5];
    brief[246]=window[1][5]<window[7][1];
    brief[247]=window[3][1]<window[1][0];
    brief[248]=window[1][7]<window[3][1];
    brief[249]=window[8][7]<window[3][7];
    brief[250]=window[8][4]<window[7][6];
    brief[251]=window[6][8]<window[4][1];
    brief[252]=window[1][6]<window[3][4];
    brief[253]=window[6][2]<window[4][3];
    brief[254]=window[2][0]<window[7][2];
    brief[255]=window[0][8]<window[4][8];
end
wire [4:0] grid_x=(eval_x-10'd4)>>5,grid_y=(eval_y-10'd4)>>5;
wire [IW-1:0] cell_index=grid_y*GX+grid_x;
wire feature_candidate=eval_pending && cap_state==3 && eval_x>=8 && eval_y>=8 && corner;

// Explicit width conversion for packed coordinates.
wire [15:0] feature_px=eval_x*4-16,feature_py=eval_y*4-16;
wire [31:0] packed_xy={feature_py,feature_px};
reg fv1=0,fv2=0;
reg [IW-1:0] fi1,fi2;
reg [7:0] fs1,fs2,previous_score;
reg [287:0] fd1,fd2;
always @(posedge aclk) begin
    if(!aresetn || arm || invalidate) begin fv1<=0;fv2<=0;end
    else begin
        fv1<=feature_candidate;fv2<=fv1;
        if(feature_candidate) begin fi1<=cell_index;fs1<=corner_score;fd1<={packed_xy,brief};end
        if(fv1) begin
            fi2<=fi1;fs2<=fs1;fd2<=fd1;
            previous_score<=cap_bank?score1[fi1]:score0[fi1];
        end
    end
end
wire commit_feature=fv2 && cap_state==3 && fi2<N && fs2>previous_score;
always @(posedge aclk) begin
    if(cap_state==1) begin
        if(cap_bank) score1[clear_index]<=0;else score0[clear_index]<=0;
    end else if(commit_feature) begin
        if(cap_bank) score1[fi2]<=fs2;else score0[fi2]<=fs2;
    end
end
always @(posedge aclk) begin
    if(commit_feature) begin
        if(cap_bank) bank1[fi2]<=fd2;
        else bank0[fi2]<=fd2;
    end
end
always @(posedge aclk) begin
    if(!aresetn) begin cap_state<=0;bank_valid<=0;features0<=0;features1<=0;x<=0;y<=0;pixels<=0;shape_bad<=0;end
    else begin
        if(invalidate) begin bank_valid<=0;cap_state<=0;features0<=0;features1<=0;end
        if(arm) begin
            cap_state<=1;clear_index<=0;cap_bank<=arm_bank;bank_valid[arm_bank]<=0;
            if(arm_bank) features1<=0;else features0<=0;
        end
        if(cap_state==1) begin
            if(clear_index==N-1) begin cap_state<=2;end else clear_index<=clear_index+1;
        end
        if(fire && collecting) begin
            if(mon_user) begin
                cap_state<=3;x<=1;y<=0;pixels<=1;shape_bad<=mon_last;
                // A second SOF invalidates the partial attempt, rather than joining frames.
                if(cap_state==3) begin cap_state<=0;bank_valid[cap_bank]<=0;end
            end else begin
                pixels<=pixels+1;
                if(mon_last) begin
                    x<=0;y<=y+1;
                    if(x!=IMAGE_WIDTH-1) shape_bad<=1;
                    if(y==IMAGE_HEIGHT-1) begin
                        cap_state<=0;
                        bank_valid[cap_bank]<=!shape_bad && x==IMAGE_WIDTH-1 && pixels+1==IMAGE_WIDTH*IMAGE_HEIGHT;
                    end
                end else begin x<=x+1;if(x>=IMAGE_WIDTH-1) shape_bad<=1;end
            end
        end
        if(commit_feature) begin
            if(cap_bank) begin
                if(previous_score==0) features1<=features1+1;
            end else begin
                if(previous_score==0) features0<=features0+1;
            end
        end
    end
end
// Sequential mutual-nearest Hamming search. Four 64-bit popcounts per candidate.
function [6:0] pop64;
    input [63:0] bits;
    integer p;
    reg [1:0] n2[31:0];reg [2:0] n4[15:0];reg [3:0] n8[7:0];
    reg [4:0] n16[3:0];reg [5:0] n32[1:0];
    begin
        for(p=0;p<32;p=p+1) n2[p]={1'b0,bits[p*2]}+{1'b0,bits[p*2+1]};
        for(p=0;p<16;p=p+1) n4[p]={1'b0,n2[p*2]}+{1'b0,n2[p*2+1]};
        for(p=0;p<8;p=p+1) n8[p]={1'b0,n4[p*2]}+{1'b0,n4[p*2+1]};
        for(p=0;p<4;p=p+1) n16[p]={1'b0,n8[p*2]}+{1'b0,n8[p*2+1]};
        for(p=0;p<2;p=p+1) n32[p]={1'b0,n16[p*2]}+{1'b0,n16[p*2+1]};
        pop64={1'b0,n32[0]}+{1'b0,n32[1]};
    end
endfunction
reg [3:0] ms=0;
assign match_busy=ms!=0 || match_start;
reg [15:0] mi=0,mj=0,idx=0,best_j=0;
reg [8:0] best=511,second=511,distance_acc=0;
reg [1:0] chunk=0;
reg [295:0] left_slot,right_slot;
reg [255:0] xor_bits;
reg [8:0] reverse_dist[N-1:0],candidate_dist[N-1:0],candidate_second[N-1:0];
reg [15:0] reverse_index[N-1:0],candidate_index[N-1:0];
reg [31:0] candidate_xy[N-1:0];
reg [8:0] rd_dist,final_dist,final_second;
reg [15:0] rd_index,final_j;
reg [31:0] final_xy;
reg [295:0] filter_left;
always @(posedge aclk) begin
    if(!aresetn) begin ms<=0;match_done<=0;match_count<=0;end
    else begin
        if(arm || invalidate) begin match_done<=0;match_count<=0;end
        if(match_start) begin ms<=1;idx<=0;match_done<=0;match_count<=0;end
        case(ms)
        1:begin
            reverse_dist[idx]<=511;reverse_index[idx]<=65535;
            candidate_dist[idx]<=511;candidate_second[idx]<=511;candidate_index[idx]<=65535;
            result_status[idx]<=0;
            if(idx==N-1) begin ms<=2;mi<=0;end else idx<=idx+1;
        end
        2:begin left_slot<={score0[mi],bank0[mi]};ms<=3;end
        3:begin
            if(left_slot[295:288]==0) ms<=10;
            else begin mj<=0;best<=511;second<=511;best_j<=65535;ms<=4;end
        end
        4:begin right_slot<={score1[mj],bank1[mj]};rd_dist<=reverse_dist[mj];ms<=5;end
        5:begin
            if(right_slot[295:288]==0) ms<=8;
            else begin xor_bits<=left_slot[255:0]^right_slot[255:0];chunk<=0;distance_acc<=0;ms<=6;end
        end
        6:begin
            distance_acc<=distance_acc+pop64(xor_bits[63:0]);xor_bits<=xor_bits>>64;
            if(chunk==3) ms<=7;else chunk<=chunk+1;
        end
        7:begin
            if(distance_acc<best) begin second<=best;best<=distance_acc;best_j<=mj;candidate_xy[mi]<=right_slot[287:256];end
            else if(distance_acc<second) second<=distance_acc;
            if(distance_acc<rd_dist) begin reverse_dist[mj]<=distance_acc;reverse_index[mj]<=mi;end
            ms<=8;
        end
        8:if(mj==N-1) ms<=9;else begin mj<=mj+1;ms<=4;end
        9:begin candidate_dist[mi]<=best;candidate_second[mi]<=second;candidate_index[mi]<=best_j;ms<=10;end
        10:if(mi==N-1) begin idx<=0;ms<=11;end else begin mi<=mi+1;ms<=2;end
        11:begin
            final_dist<=candidate_dist[idx];final_second<=candidate_second[idx];final_j<=candidate_index[idx];
            final_xy<=candidate_xy[idx];filter_left<={score0[idx],bank0[idx]};ms<=12;
        end
        12:begin
            if(final_j<N) rd_index<=reverse_index[final_j];else rd_index<=65535;
            ms<=13;
        end
        13:begin
            result0[idx]<=filter_left[287:256];result1[idx]<=final_xy;
            if(final_j<N && rd_index==idx && final_dist<=64 && final_second<511 &&
               final_dist*100<final_second*75 && filter_left[295:288]!=0) begin
                result_status[idx]<=32'h80000000|final_dist;match_count<=match_count+1;
            end else result_status[idx]<=0;
            if(idx==N-1) begin ms<=0;match_done<=1;end else begin idx<=idx+1;ms<=11;end
        end
        default:;
        endcase
    end
end
endmodule
