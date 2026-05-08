function [File,isQuit] = setOscilloscope2_Tek(File)
try
    instrreset;
catch
end
%% Constants
BUTTON_1 = '1';
BUTTON_2 = '2';
MAX_ATTEMPTS = 3;

%% Variables
isQuit = false;

% Structure
Info = struct( ...
    'Channel','CH1', ...
    'Coupling','DC', ...
    'VerticalScale',0, ...
    'HorizontalScale',0, ...
    'DataLength',2500, ...
    'AcquisitionMode','Average mode');
Settings = struct( ...
    'DataWidth',[], ...
    'Bits',[], ...
    'DataEncoding','BIN', ...
    'BinaryFormat',2, ...
    'BinaryOrder','MSB', ...
    'DataLength',2500, ...
    'Info',Info, ...
    'ReferenceFormat','Y', ...
    'Interval',0, ...
    'TriggerOffset',0, ...
    'HorizontalStart',0, ...
    'HorizontalUnit','s', ...
    'VerticalScaleFactor',0, ...
    'WaveformConversion',0, ...
    'VerticalPosition',0, ...
    'VerticalUnit','V', ...
    'Time',[], ...
    'Status',0);
Oscilloscope = struct( ...
    'Make','', ...
    'Model','', ...
    'Serial','', ...
    'Firmware','', ...
    'Resource','', ...
    'Info',[], ...
    'Trigger','', ...
    'Settings',Settings, ...
    'Object',[]);
File.Oscilloscope = Oscilloscope;

%% Number of Oscilloscopes
questNum = questdlg( ...
    'How many oscilloscopes?', ...
    'Number of Oscilloscopes', ...
    BUTTON_1,BUTTON_2,BUTTON_2);
numOfDevices = str2double(questNum);

%% Searching for resources
fprintf('Searching for resources...');
startTime = tic;
try
    resource = instrhwinfo('visa','ni'); %#ok<INSTHWV> % search for USB resource
    type = 'ni';
catch
    resource = instrhwinfo('visa','tek'); %#ok<INSTHWV> % search for USB resource
    type = 'tek';
end
resourceName_cell = resource.ObjectConstructorName;  % resource name (cell)
usb_tf = contains(resourceName_cell,'USB','IgnoreCase',true);
usb_idx = find(usb_tf);
isUSB = any(usb_idx);
strPattern = {'visa(',type,', ',');',''''};
resourceName_list = erase(resourceName_cell,strPattern);
% empty_idx = cellfun(@isempty,resourceName_list); % identify the empty cells
% resourceName_list(empty_idx) = [];
[endTime,unit] = getEndTime(startTime);
fprintf('found (%.2f %s)\n',endTime,unit);                           % USB resource found

%% Selecting Resource
fprintf('Selecting resource...');
startTime = tic;
switch numOfDevices
    case 1
        if isUSB
            baudRate = 9600;
            resourceName = resourceName_list{usb_idx};
            oscilloscope = visa(type,resourceName);
        else
            baudRate = 19200;
            isConnected = false;
            numOfResources = length(resourceName_list);
            resourceNum_arr = flip(1:numOfResources);
            for resourceNum = resourceNum_arr
                resourceName = resourceName_list{resourceNum};
                for attemptNum = 1:MAX_ATTEMPTS
                    oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
                    try
                        fopen(oscilloscope);
                        status = oscilloscope.Status;
                        if contains2(status,'open')
                            fclose(oscilloscope);
                            isConnected = true;
                            break;
                        end
                    catch
                    end
                end
                if isConnected
                    break;
                end
            end
        end
        File.Oscilloscope(1).Resource = resourceName;  % resource name
        File.Oscilloscope(1).Object = oscilloscope;
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.2f %s)\n',resourceName,endTime,unit);   % info confirmed
    case 2
        for deviceNum = 1:numOfDevices
            isConnected = false;
            % Dialog box
            switch deviceNum
                case 1
                    baudRate = 9600;
                    resourceName = resourceName_list{usb_idx};
                    oscilloscope = visa(type,resourceName);
                    isConnected = true;
                case 2
                    baudRate = 19200;
                    numOfResources = length(resourceName_list);
                    resourceNum_arr = flip(1:numOfResources);
                    for resourceNum = resourceNum_arr
                        resourceName = resourceName_list{resourceNum};
                        for attemptNum = 1:MAX_ATTEMPTS
                            oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
                            try
                                fopen(oscilloscope);
                                status = oscilloscope.Status;
                                if contains2(status,'open')
                                    fclose(oscilloscope);
                                    isConnected = true;
                                    break;
                                end
                            catch
                            end
                        end
                        if isConnected
                            break;
                        end
                    end
            end
            if isConnected
                File.Oscilloscope(deviceNum).Resource = resourceName;  % resource name
                File.Oscilloscope(deviceNum).Object = oscilloscope;
            end
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);   % info confirmed
end
terminator = 'LF';

%% Connecting to Oscilloscope
fprintf('Connecting to oscilloscope...');	% connect to oscilloscope
bufferSize = 20e3;
ENDIAN_LITTLE = 'littleEndian';
ENDIAN_BIG = 'bigEndian';
byteOrder = ENDIAN_BIG;
timeout = 10;
startTime = tic;
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    resourceName = File.Oscilloscope(deviceNum).Resource;
    isUSB = contains2(resourceName,'usb');
    oscilloscope.InputBufferSize = bufferSize;         % expand data buffer for detailed waveform
    oscilloscope.ByteOrder = byteOrder;
    oscilloscope.Timeout = timeout;
    if ~isUSB
        oscilloscope.BaudRate = baudRate;
        oscilloscope.FlowControl = 'hardware';
        oscilloscope.Terminator = terminator;
    end
    fopen(oscilloscope); % start oscillocope
    File.Oscilloscope(deviceNum).Object = oscilloscope;
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);                           % selected resource

%% Oscilloscope Information
fprintf('Getting information...');
if numOfDevices > 1
    fprintf('\n');
end
startTime = tic;
for deviceNum = 1:numOfDevices
    if numOfDevices > 1
        fprintf('Device %d...',deviceNum);
    end
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    id_char = {};
    while isempty(id_char)
        try
            fprintf(oscilloscope,'*IDN?');
            %         pause(1);
            id_char = fgetl(oscilloscope);
            if ~isempty(id_char)
                id_cell = strsplit(id_char,',');
                numOfID = length(id_cell);
                make = id_cell{1};
                File.Oscilloscope(deviceNum).Make = make;
                model = id_cell{2};
                File.Oscilloscope(deviceNum).Model = model;
                switch numOfID
                    case 4
                        serial = id_cell{3};
                        File.Oscilloscope(deviceNum).Serial = serial;
                        firmware = strsplit(id_cell{4});
                        firmwareVersion = firmware{2};
                    case 3
                        firmware = strsplit(id_cell{3});
                        firmwareVersion = firmware{2};
                end
                File.Oscilloscope(deviceNum).Firmware = firmwareVersion;
                [endTime,unit] = getEndTime(startTime);
                fprintf('%s %s (%.2f %s)\n',make,model,endTime,unit);
            else
                pause(5);
            end
        catch
            pause(5);
        end
    end
end

%% RS232 Communication
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    resourceName = File.Oscilloscope(deviceNum).Resource;
    isUSB = contains2(resourceName,'usb');
    if ~isUSB
        fprintf('Setting up RS232 communication...');
        startTime = tic;
        baudRate_use = sprintf('RS232:BAUd %d',baudRate);
        fprintf(oscilloscope,baudRate_use);
        fprintf(oscilloscope,'RS232:HARDFlagging ON');
        %     fprintf(oscilloscope,'RS232:SOFTFlagging OFF');
        fprintf(oscilloscope,'RS232:TRANsmit:TERMinator LF');
        fprintf(oscilloscope,'RS232:PARity NONe');
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
    end
end

%% Oscilloscope Channels
[File,isQuit] = getChannels2_Tek(File);
if isQuit
    fprintf('\nQuitting...\n\n'); % quitting
    return;                     % exit program
end

%% Acquisition
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    switch numOfDevices
        case 1
            acqMode = 'Setting acquisition mode...';
            avgMode = 'Setting average acquisition mode...';
        case 2
            model = File.Oscilloscope(deviceNum).Model;
            acqMode = sprintf('Setting acquisition mode for %s...',model);
            avgMode = sprintf('Setting average acquisition mode for %s...',model);
    end
    % Set up acquisition mode
    fprintf(acqMode);
    startTime = tic;
    %  phaseWidth = File.Parameters.PhaseWidth1;
    % if phaseWidth >= 100
    fprintf(oscilloscope,'ACQuire:MODe AVErage');  % set waveform acquisition to averages
    [endTime,unit] = getEndTime(startTime);
    fprintf('AVERAGE (%.2f %s)\n',endTime,unit);
    
    % Average Mode
    fprintf(avgMode);
    startTime = tic;
    numOfSamples = 128;
    numOfSamples_use = sprintf('ACQuire:NUMAvg %d',numOfSamples);
    fprintf(oscilloscope,numOfSamples_use);    % set number of wavforms for average
    [endTime,unit] = getEndTime(startTime);
    fprintf('%d samples (%.2f %s)\n',numOfSamples,endTime,unit);  % info confirmed
    fprintf(oscilloscope,'ACQuire:STAte RUN');
end

%% Headers
fprintf('Setting headers...');
startTime = tic;
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    fprintf(oscilloscope,'HEADer OFF');
end
[endTime,unit] = getEndTime(startTime);
fprintf('OFF (%.2f %s)\n',endTime,unit);

%% Data Encoding
% ASCII = 'ASCii';
RIBIN = 'RIBinary';
% RPBIN = 'RPBinary';
SRIBIN = 'SRIbinary';
% SRPBIN = 'SRPbinary';
switch byteOrder
    case ENDIAN_BIG
        binOrder = 'MSB';
        binaryEncoding = RIBIN;
    case ENDIAN_LITTLE
        binOrder = 'LSB';
        binaryEncoding = SRIBIN;
end
dataEncoding = binaryEncoding;
dataEncoding_use = sprintf('DATa:ENCdg %s',dataEncoding);
fprintf('Setting data encoding...');
startTime = tic;
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    dataEncoding_check = '';
    while ~contains2(dataEncoding_check,dataEncoding)
        fprintf(oscilloscope,dataEncoding_use);  % encoding for sensible data output
        fprintf(oscilloscope,'DATa:ENCdg?');
        dataEncoding_check = fgetl2(oscilloscope);
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('%s (%.2f %s)\n',dataEncoding,endTime,unit);

%% Binary Order
if contains2(dataEncoding,'BIN')
    fprintf('Setting binary order...');
    startTime = tic;
    for deviceNum = 1:numOfDevices
        oscilloscope = File.Oscilloscope(deviceNum).Object;
        binOrder_check = '';
        while ~contains2(binOrder,binOrder_check)
            binOrder_use = sprintf('WFMPre:BYT_Or %s',binOrder);
            fprintf(oscilloscope,binOrder_use);
            fprintf(oscilloscope,'WFMPre:BYT_Or?');
            binOrder_check = fgetl2(oscilloscope);
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',binOrder_check,endTime,unit);
end

%% Data Width
fprintf('Setting data width...');
startTime = tic;
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    if contains2(dataEncoding,'ASC')
        dataWidth = 1;
    else
        dataWidth = 2;
    end
    dataWidth_use = sprintf('DATa:WIDth %d',dataWidth);
    % Check
    dataWidth_check = [];
    while ~isequal(dataWidth_check,dataWidth)
        fprintf(oscilloscope,dataWidth_use);
        fprintf(oscilloscope,'DATa:WIDth?');
        dataWidth_raw = fgetl2(oscilloscope);
        dataWidth_check = str2double(dataWidth_raw);
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('%d byte (%.2f %s)\n',dataWidth,endTime,unit);

%% Oscilloscope Display
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    if numOfDevices > 1
        model = File.Oscilloscope(deviceNum).Model;
        fprintf('Setting display for %s...\n',model);
    end

    % Display
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    for channel_idx = 1:numOfChannels
        scopeChannel = channelSelect_cell{channel_idx};
        % Display
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s for display...',scopeChannel);
        startTime = tic;
        status = '1';
        status_check = '';
        status_use = sprintf('SELect:%s ON',scopeChannel);
        status_query = sprintf('SELect:%s?',scopeChannel);
        while ~contains2(status_check,status)
            fprintf(oscilloscope,status_use);
            fprintf(oscilloscope,status_query);
            status_check = fgetl2(oscilloscope);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('ON (%.2f %s)\n',endTime,unit)
    
        % Coupling
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s coupling...',scopeChannel);
        startTime = tic;
        coupling = 'DC';
        coupling_check = '';
        coupling_use = sprintf('%s:COUPling %s',scopeChannel,coupling);
        coupling_query = sprintf('%s:COUPling?',scopeChannel);
        while ~contains2(coupling_check,coupling)
            fprintf(oscilloscope,coupling_use);	% coupling on voltage
            fprintf(oscilloscope,coupling_query);
            coupling_check = fgetl2(oscilloscope);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.2f %s)\n',coupling,endTime,unit);
    
        % Unit
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s unit...',scopeChannel);
        startTime = tic;
        channelUnit = 'V';
        channelUnit_check = '';
        channelUnit_use = sprintf('%s:YUNit "%s"',scopeChannel,channelUnit);
        channelUnit_query = sprintf('%s:YUNit?',scopeChannel);
        while ~contains2(channelUnit_check,channelUnit)
            fprintf(oscilloscope,channelUnit_use);
            fprintf(oscilloscope,channelUnit_query);
            channelUnit_check = fgetl2(oscilloscope);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('"%s" (%.2f %s)\n',channelUnit,endTime,unit);
    
        % Probe
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s probe...',scopeChannel);
        startTime = tic;
        probe = 1;
        probe_check = 0;
        switch channelUnit
            case 'A'
                probeType = 'CURRENTPROBe';
            case 'V'
                probeType = 'PRObe';
        end
        probe_use = sprintf('%s:%s 1',scopeChannel,probeType);
        probe_query = sprintf('%s:%s?',scopeChannel,probeType);
        while ~isequal(probe_check,probe)
            fprintf(oscilloscope,probe_use);
            fprintf(oscilloscope,probe_query);
            probe_raw = fgetl2(oscilloscope);
            probe_check = str2double(probe_raw);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%dX (%.2f %s)\n',probe,endTime,unit);
    
        % Polarity
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s polarity...',scopeChannel);
        startTime = tic;
        invert = 'OFF';
        invert_check = '';
        invert_use = sprintf('%s:INVert OFF',scopeChannel);
        invert_query = sprintf('%s:INVert?',scopeChannel);
        while ~contains2(invert_check,invert)
            fprintf(oscilloscope,invert_use);
            fprintf(oscilloscope,invert_query);
            invert_check = fgetl2(oscilloscope);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.2f %s)\n',invert,endTime,unit);
    
        % Position
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('Setting %s position...',scopeChannel);
        startTime = tic;
        position = 0;
        position_check = [];
        position_use = sprintf('%s:POSition %.2e',scopeChannel,position);
        position_query = sprintf('%s:POSition?',scopeChannel);
        while ~isequal(position_check,position)
            fprintf(oscilloscope,position_use);
            fprintf(oscilloscope,position_query);
            position_raw = fgetl2(oscilloscope);
            position_check = str2double(position_raw);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%.2e %s (%.2f %s)\n',position,channelUnit,endTime,unit);
    end
end

%% Trigger
for deviceNum = 1:numOfDevices
    make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    fprintf('Setting %s %s trigger...',make,model);
    startTime = tic;

    % Type
    trigType = 'EDGe';
    trigType_check = '';
    trigType_use = sprintf('TRIGger:MAIn:TYPe %s',trigType);
    while ~contains2(trigType_check,trigType)
        fprintf(oscilloscope,trigType_use);          % trigger type
        fprintf(oscilloscope,'TRIGger:MAIn:TYPe?');
        trigType_check = fgetl2(oscilloscope);
    end
    fprintf('EXT...');

    % Source
    trigSource = 'EXT';
    trigSource_check = '';
    trigSource_use = sprintf('TRIGger:MAIn:EDGe:SOUrce %s',trigSource);
    while ~contains2(trigSource_check,trigSource)
        fprintf(oscilloscope,trigSource_use);          % trigger source
        fprintf(oscilloscope,'TRIGger:MAIn:EDGe:SOUrce?');
        trigSource_check = fgetl2(oscilloscope);
    end
    fprintf('EXT...');

    % Slope
    trigSlope = 'RISe';
    trigSlope_check = '';
    slope_use = sprintf('TRIGger:MAIn:EDGe:SLOpe %s',trigSlope);
    while ~contains2(trigSlope_check,trigSlope)
        fprintf(oscilloscope,slope_use);  % trigger on rising slope
        fprintf(oscilloscope,'TRIGger:MAIn:EDGe:SLOpe?');
        trigSlope_check = fgetl2(oscilloscope);
    end
    fprintf('rising edge...');
    File.Oscilloscope(deviceNum).Trigger = trigSource;

    % Normal Mode
    trigMode = 'NORMal';
    trigMode_check = '';
    trigMod_use = sprintf('TRIGger:MAIn:MODe "%s"',channelUnit);
    while ~contains2(trigMode_check,trigMode)
        fprintf(oscilloscope,trigMod_use);
        fprintf(oscilloscope,'TRIGger:MAIn:MODe?');
        trigMode_check = fgetl2(oscilloscope);
    end
    fprintf('normal...');

    % Coupling
    trigCoupling = 'DC';
    trigCoupling_check = '';
    trigCoupling_use = sprintf('TRIGger:MAIn:EDGE:COUPling %s',trigCoupling);
    while ~contains2(trigCoupling_check,trigCoupling)
        fprintf(oscilloscope,trigCoupling_use);         % trigger on DC coupling
        fprintf(oscilloscope,'TRIGger:MAIn:EDGE:COUPling?');
        trigCoupling_check = fgetl2(oscilloscope);
    end
    fprintf('DC');

    [endTime,unit] = getEndTime(startTime);
    fprintf(' (%.2f %s)\n',endTime,unit);
end

%% Settings
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    instr = instrhwinfo(oscilloscope);
    File.Oscilloscope(deviceNum).Info = instr;
    File = getSettings_Tek(File,deviceNum,'all');
end

end