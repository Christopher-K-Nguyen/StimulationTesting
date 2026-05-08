function File = getSettings2(File,varargin)
%% Variables
numOfVar = length(varargin);
if numOfVar > 1
    var1 = varargin{1};
    var2 = varargin{2};
    if isnumeric(var1)
        deviceNum = var1;
        source = var2;
    else
        deviceNum = var2;
        source = var1;
    end
else
    deviceNum = 1;
    source = varargin{1};
end

oscilloscope = File.Oscilloscope(deviceNum).Object;      % oscilloscope
setStatus_Tek(File,deviceNum,'open');
tag = '';
isChannelOn = true;

%% Function
startTime = tic;
if isempty(source)
    source = '';
end
source = char(source);
source_upper = upper(source);
% make = File.Oscilloscope(deviceNum).Make;
model = File.Oscilloscope(deviceNum).Model;
switch source_upper
    case {'CH1','CH2','CH3','CH4'}
        tag = source_upper;
        fprintf('Getting %s %s settings...',model,tag);
        source = tag;
    case {'T','TIME'}
        fprintf('Getting %s time settings...',model);
        tag = 'time';
        source = 'CH1';
    case {'','EVERYTHING','ALL',[]}
        fprintf('Getting %s waveform settings...',model);
        tag = '';
        source = 'CH1';
end

source_check = '';
source_use = sprintf('DATa:SOUrce %s',source);
while ~contains2(source,source_check)
    fprintf(oscilloscope,source_use);
    fprintf(oscilloscope,'DATa:SOUrce?');
    source_check = fgetl2(oscilloscope);
end

switch tag
    case {'CH1','CH2','CH3','CH4'}
        channelStatus_use = sprintf('SELect:%s?',tag);
        if deviceNum == 2
            attempts = 2;
        else
            attempts = 1;
        end
        for attemptNum = 1:attempts
            fprintf(oscilloscope,channelStatus_use);
            channelStatus = fgetl2(oscilloscope);
            if deviceNum == 2
                pause(0.5);
            end
        end
        isChannelOn = contains2(channelStatus,'1');
        if isChannelOn
            % Conversion factor
            YZEro = [];
            while isempty(YZEro) || isnan(YZEro) || YZEro == 1
                fprintf(oscilloscope,'WFMPre:YZEro?');	% query conversion factor
                YZEro_raw = fgetl2(oscilloscope);
                YZEro = str2double(YZEro_raw); % get conversion factor number
            end
            File.Oscilloscope(deviceNum).Settings.WaveformConversion = YZEro;

            % Vertical scale factor
            YMUlt = [];
            while isempty(YMUlt) || isnan(YMUlt)  || ~any(YMUlt) || abs(YMUlt) > 1
                fprintf(oscilloscope,'WFMPre:YMUlt?');	% query vertical scale factor
                YMUlt_raw = fgetl2(oscilloscope);
                YMUlt = str2double(YMUlt_raw);% get vertical scale factor number
            end
            File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor = YMUlt;

            % Vertical position
            YOFf = [];
            while isempty(YOFf) || isnan(YOFf) || ~(rem(YOFf,1) == 0 || ~any(YOFf))
                fprintf(oscilloscope,'WFMPre:YOFf?');	% query vertical position
                YOFf_raw = fgetl2(oscilloscope);
                YOFf = str2double(YOFf_raw);  % get vertical position number
            end
            File.Oscilloscope(deviceNum).Settings.VerticalPosition = YOFf;
        end
    case 'time'
        % get time of first data point
        XZEro = [];
        while isempty(XZEro) || XZEro == 0 || isnan(XZEro) || abs(XZEro) > 0.1
            fprintf(oscilloscope,'WFMPre:XZEro?');% query time of first data point
            XZEro_raw = fgetl2(oscilloscope);
            XZEro = str2double(XZEro_raw);	 %#ok<*ST2NM> % get time of first data point
        end
        File.Oscilloscope(deviceNum).Settings.HorizontalStart = XZEro;

        % get horizontal interval number
        XINcr = [];
        while isempty(XINcr) || XINcr == 0 || isnan(XINcr) || XINcr == XZEro  || abs(XZEro) > 0.1
            fprintf(oscilloscope,'WFMPre:XINcr?');        % query horitzontal sampling interval
            XINcr_raw = fgetl2(oscilloscope);
            XINcr = str2double(XINcr_raw);	% get horizontal interval number
        end
        File.Oscilloscope(deviceNum).Settings.Interval = XINcr;

        % get query number of points in CURve
        fprintf(oscilloscope,'WFMPre:NR_Pt?');% query waveform data
        NR_Pt = 0;
        while NR_Pt < 100
            NR_Pt_raw = fgetl2(oscilloscope);
            NR_Pt = str2double(NR_Pt_raw);
            File.Oscilloscope(deviceNum).Settings.DataLength = NR_Pt;
        end

    case ''
        data_len = 1;
        while data_len <= 1
            fprintf(oscilloscope,'WFMPre?');% query waveform data
            data = fgetl2(oscilloscope);
            data_fixed = erase(data,{'"',newline});
            data_formated = strsplit(data_fixed,';');
            data_len = length(data_formated);
        end

        %{
        BYT_Nr <NR1>; % Data width
        BIT_Nr <NR1>; % Bits per point
        ENCdg { ASC | BIN }; % Data encoding
        BN_Fmt { RI | RP }; % Binary format
        BYT_Or { LSB | MSB }; % Binary transfer order
        NR_Pt <NR1>; %  % Data length
        WFID <Qstring>  % Waveform info
        PT_FMT {ENV | Y}; % Reference format
        XINcr <NR3>;    % Sampling period
        PT_Off <NR1>;   % Trigger offset
        XZEro <NR3>;    % Horizontal start
        XUNit<QString>; % Horizontal unit
        YMUlt <NR3>;    % Vertical scale factor
        YZEro <NR3>;    % Waveform conversion factor
        YOFf <NR3>;     % Vertical position
        YUNit <QString>
        %}

        % Data Width
        dataWidth_raw = data_formated{1};
        dataWidth = str2double(dataWidth_raw);
        while isempty(dataWidth) || ~ismember(dataWidth,[1 2])
            fprintf(oscilloscope,'DATa:WIDth?');
            dataWidth_raw = fgetl2(oscilloscope);
            dataWidth = str2double(dataWidth_raw);
        end
        File.Oscilloscope(deviceNum).Settings.DataWidth = dataWidth;

        % Bits Per Point
        bits = str2double(data_formated{2});
        while isempty(bits) || ~ismember(bits,[8 16])
            fprintf(oscilloscope,'WFMPre:BIT_Nr?');
            bits_raw = fgetl2(oscilloscope);
            bits = str2double(bits_raw);
        end
        File.Oscilloscope(deviceNum).Settings.Bits = bits;

        % Data Encoding
        dataEncoding = data_formated{3};
        while isempty(dataEncoding) ||~contains2(dataEncoding,{'ASC','BIN'})
            fprintf(oscilloscope,'WFMPre:ENCdg?');
            dataEncoding_raw = fgetl2(oscilloscope);
            dataEncoding = str2double(dataEncoding_raw);
        end
        File.Oscilloscope(deviceNum).Settings.DataEncoding = dataEncoding;

        % Binary Format
        binFormat = data_formated{4};
        while isempty(binFormat) || ~contains(binFormat,{'RI','RP'})
            fprintf(oscilloscope,'WFMPre:BN_Fmt?');
            binFormat = fgetl2(oscilloscope);
        end
        File.Oscilloscope(deviceNum).Settings.BinaryFormat = binFormat;

        % Binary Order
        binOrder = data_formated{5};
        while isempty(binOrder) || ~contains(binOrder,{'MSB','LSB'})
            fprintf(oscilloscope,'WFMPre:BYT_Or?');
            binOrder = fgetl2(oscilloscope);
        end
        File.Oscilloscope(deviceNum).Settings.BinaryOrder = binOrder;

        % Data Length
        dataLength_raw = data_formated{6};
        dataLength = str2double(dataLength_raw);
        while isempty(dataLength) || dataLength < 100
            %             dataLength = 2500;
            fprintf(oscilloscope,'WFMPre:NR_Pt?');
            dataLength_raw = fgetl2(oscilloscope);
            dataLength = str2double(dataLength_raw);
        end
        File.Oscilloscope(deviceNum).Settings.DataLength = dataLength;

        % Reference Format
        refFormat = data_formated{8};
        while isempty(refFormat) || ~contains(refFormat,{'Y','ENV'})
            fprintf(oscilloscope,'WFMPre:PT_Fmt');
            refFormat = fgetl2(oscilloscope);
        end
        File.Oscilloscope(deviceNum).Settings.ReferenceFormat = refFormat;

        interval_raw = data_formated{9};
        interval = str2double(interval_raw);
        while isempty(interval) || interval == 0 || isnan(interval)
            fprintf(oscilloscope,'WFMPre:XINcr?');
            interval_raw = fgetl2(oscilloscope);
            interval = str2double(interval_raw);
        end
        File.Oscilloscope(deviceNum).Settings.Interval = interval;

        % Trigger Offset
        trigOffset_raw = data_formated{10};
        trigOffset = str2double(trigOffset_raw);
        while isempty(interval) || trigOffset ~= 0
            fprintf(oscilloscope,'WFMPre:PT_Off?');
            trigOffset_raw = fgetl2(oscilloscope);
            trigOffset = str2double(trigOffset_raw);
        end
        File.Oscilloscope(deviceNum).Settings.TriggerOffset = trigOffset;

        % Horizontal Start
        XZEro_raw = data_formated{11};
        XZEro = str2double(XZEro_raw);
        while isnan(XZEro)
            fprintf(oscilloscope,'WFMPre:XZEro?');% query time of first data point
            XZEro_raw = fgetl2(oscilloscope);
            XZEro = str2double(XZEro_raw);	 %#ok<*ST2NM> % get time of first data point
        end
        File.Oscilloscope(deviceNum).Settings.HorizontalStart = XZEro;

        % Horizontal Unit
        XUNit = data_formated{12};
        while ~contains2(XUNit,{'s','Hz'})
            fprintf(oscilloscope,'WFMPre:XUNit?');
            XUNit = fgetl2(oscilloscope);
        end
        File.Oscilloscope(deviceNum).Settings.HorizontalUnit = XUNit;

        % Vertical Scale Factor
        YMUlt_raw = data_formated{13};
        YMUlt = str2double(YMUlt_raw);
        while isempty(YMUlt) || isnan(YMUlt) || ~any(YMUlt) || abs(YMUlt) > 1
            fprintf(oscilloscope,'WFMPre:YMUlt?');	% query vertical scale factor
            YMUlt_raw = fgetl2(oscilloscope);
            YMUlt = str2double(YMUlt_raw);% get vertical scale factor number
        end
        File.Oscilloscope(deviceNum).Settings.VerticalScaleFactor = YMUlt;

        % Waveform Conversion
        %~any(YZEro)
        YZEro_raw = data_formated{14};
        YZEro = str2double(YZEro_raw);
        while isempty(YZEro) || isnan(YZEro) || YZEro == 1
            fprintf(oscilloscope,'WFMPre:YZEro?');	% query conversion factor
            YZEro_raw = fgetl2(oscilloscope);
            YZEro = str2double(YZEro_raw); % get conversion factor number
        end
        File.Oscilloscope(deviceNum).Settings.WaveformConversion = YZEro;

        % Vertical Position
        YOFf_raw = data_formated{15};
        YOFf = str2double(YOFf_raw);
        while isempty(YOFf) || isnan(YOFf) || ~(rem(YOFf,1) == 0 || ~any(YOFf))
            fprintf(oscilloscope,'WFMPre:YOFf?');	% query vertical position
            YOFf_raw = fgetl2(oscilloscope);
            YOFf = str2double(YOFf_raw);  % get vertical position number
        end
        File.Oscilloscope(deviceNum).Settings.VerticalPosition = YOFf;

        % Vertical Unit
        YUNit_raw = data_formated{16};
        YUNit = erase(YUNit_raw,newline);
        while ~contains(YUNit,{'Volts','U','dB'})
            fprintf(oscilloscope,'WFMPre:YUNit?');
            YUNit = fgetl2(oscilloscope);
        end
        File.Oscilloscope(deviceNum).Settings.VerticalUnit = YUNit;

        wfid = data_formated{7};
        wfid_formatted = strsplit(wfid,', ');
        wfid_len = length(wfid_formatted);
        while ~iscell(wfid_formatted) || wfid_len < 3
            fprintf(oscilloscope,' WFMPre:WFId?');
            wfid = fgetl2(oscilloscope);
            wfid_formatted = strsplit(wfid,', ');
            wfid_len = length(wfid_formatted);
        end
        File.Oscilloscope(deviceNum).Settings.Info.Channel = upper(wfid_formatted{1});
        coupling = wfid_formatted{2};
        coupling_formatted = strsplit(coupling);
        File.Oscilloscope(deviceNum).Settings.Info.Coupling = coupling_formatted{1};
        verticalScale = wfid_formatted{3};
        verticalScale_formatted = strsplit(verticalScale);
        File.Oscilloscope(deviceNum).Settings.Info.VerticalScale = str2double(verticalScale_formatted{1});
        horizontalScale = wfid_formatted{4};
        horizontalScale_formatted = strsplit(horizontalScale);
        File.Oscilloscope(deviceNum).Settings.Info.HorizontalScale = str2double(horizontalScale_formatted{1});
        dataLength = wfid_formatted{5};
        dataLength_formatted = strsplit(dataLength);
        File.Oscilloscope(deviceNum).Settings.Info.DataLength = str2double(dataLength_formatted{1});
        acquisitionMode = wfid_formatted{6};
        acquisitionMode_formatted = strsplit(acquisitionMode);
        File.Oscilloscope(deviceNum).Settings.Info.AcquisitionMode = acquisitionMode_formatted{1};
end
File.Oscilloscope(deviceNum).Settings.Status = isChannelOn;
[endTime,unit] = getEndTime(startTime);
if isChannelOn
    fprintf('OK (%.2f %s)\n',endTime,unit);
else
    fprintf('NONE (%.2f %s)\n',endTime,unit);
end

end