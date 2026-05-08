function [File,isQuit] = setOscilloscope(File)
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
% questNum = questdlg( ...
%     'How many oscilloscopes?', ...
%     'Number of Oscilloscopes', ...
%     BUTTON_1,BUTTON_2,BUTTON_1);
% numOfDevices = str2double(questNum);
numOfDevices = 1;

%% Searching for resources
fprintf('Searching for resources...');
startTime = tic;
resource = instrhwinfo('visa','ni'); %#ok<INSTHWV> % search for USB resource
resourceName_cell = resource.ObjectConstructorName;  % resource name (cell)
usb_tf = contains(resourceName_cell,'USB','IgnoreCase',true);
isUSB = any(usb_tf);
[endTime,unit] = getEndTime(startTime);
fprintf('found (%.2f %s)\n',endTime,unit);                           % USB resource found

%% Selecting Resource
fprintf('Selecting resource...');
startTime = tic;
switch numOfDevices
    case 1
        if isUSB
            baudRate = 9600;
            oscilloscope = eval(resourceName_cell{usb_tf});
        else
            baudRate = 19200;
            isConnected = false;
            numOfResources = length(resourceName_cell);
            resourceNum_arr = flip(1:numOfResources);
            for resourceNum = resourceNum_arr
                for attemptNum = 1:MAX_ATTEMPTS
                    visa_use = resourceName_cell{usb_tf};
                    oscilloscope = eval(visa_use);
                    try
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
        resourceName = oscilloscope.RsrcName;
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
                    visa_use = resourceName_cell{usb_tf};
                    oscilloscope = eval(visa_use);
                    isConnected = true;
                case 2
                    baudRate = 19200;
                    numOfResources = length(resourceName_cell);
                    resourceNum_arr = flip(1:numOfResources);
                    for resourceNum = resourceNum_arr
                        for attemptNum = 1:MAX_ATTEMPTS
                            % oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
                            visa_use = resourceName_cell{resourceNum};
                            oscilloscope = eval(visa_use);
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
                resourceName = oscilloscope.RsrcName;
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
    File.Oscilloscope(deviceNum).Object = oscilloscope;
    fopen(oscilloscope);
    % fprintf(oscilloscope,'AUTOSet:ENABLE');
    % File = setScopeStatus(File,'open');
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
    try
        while isempty(id_char)
            try
                % fprintf(oscilloscope,'*CLS');
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
                    fprintf(oscilloscope,'*CLS');
                    % Run the PowerShell command and export to CSV for full output
                    system('powershell "Get-WmiObject Win32_PnPEntity | Select-Object Status, Class, DeviceID, Name | Export-Csv -Path devices.csv -NoTypeInformation"');

                    % Read the CSV file into MATLAB
                    opts = detectImportOptions('devices.csv','ReadVariableNames',true);  % Auto-detect column headers
                    devicesTable = readtable('devices.csv', opts);

                    deviceID_cell = devicesTable.DeviceID;
                    instr_tf = containsi(deviceID_cell,'IVI');
                    deviceStatus = char(devicesTable.Status(instr_tf));
                    deviceName = char(devicesTable.Name(instr_tf));
                    deviceID = char(devicesTable.DeviceID(instr_tf));

                    % Path to the DevCon executable
                    devconPath = '"C:\Program Files (x86)\Windows Kits\10\Tools\10.0.26100.0\x64\devcon.exe"';  % Update this path

                    % Disable the USB device
                    system([devconPath ' disable "' deviceID '"']);

                    % Enable the USB device (re-enabling it)
                    system([devconPath ' enable "' deviceID '"']);

                    % Check device status
                    system([devconPath ' status "' deviceID '"']);
                end
            catch
                fprintf(oscilloscope,'*CLS');
                pause(5);
            end
            timeLong = toc(startTime);
            if timeLong > timeout * 2
                break;
            end
        end
    catch
        isQuit = true;
    end
    timeLong = toc(startTime);
    if timeLong > timeout * 2
        isQuit = true;
        fprintf('Timeout (%.2f s)...',timeLong); % quitting
        break;
    end
end
if isQuit
    fprintf('Quitting...');
    return;                     % exit program
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
[File,isQuit] = getOscilloscopeChannels(File);
if isQuit
    fprintf('\nQuitting...\n\n'); % quitting
    return;                     % exit program
end

%% Acquisition
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    switch numOfDevices
        case 1
            acqMode = 'Setting acquisition mode...';
            avgMode = 'Setting average acquisition mode...';
        case 2
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
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    if isTPS
        dataEncoding_check = '';
        while ~contains2(dataEncoding_check,dataEncoding)
            fprintf(oscilloscope,dataEncoding_use);  % encoding for sensible data output
            fprintf(oscilloscope,'DATa:ENCdg?');
            dataEncoding_check = fgetl2(oscilloscope);
        end
    else
        fprintf(oscilloscope,dataEncoding_use);  % encoding for sensible data output
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
        model = File.Oscilloscope(deviceNum).Model;
        isTPS = contains2(model,'tps');
        binOrder_use = sprintf('WFMPre:BYT_Or %s',binOrder);
        if isTPS
            binOrder_check = '';
            while ~contains2(binOrder,binOrder_check)
                fprintf(oscilloscope,binOrder_use);
                fprintf(oscilloscope,'WFMPre:BYT_Or?');
                binOrder_check = fgetl2(oscilloscope);
            end
        else
            fprintf(oscilloscope,binOrder_use);
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',binOrder,endTime,unit);
end

%% Data Width
fprintf('Setting data width...');
startTime = tic;
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    if contains2(dataEncoding,'ASC')
        dataWidth = 1;
    else
        dataWidth = 2;
    end
    dataWidth_use = sprintf('DATa:WIDth %d',dataWidth);
    if isTPS
        dataWidth_check = [];
        while ~isequal(dataWidth_check,dataWidth)
            fprintf(oscilloscope,dataWidth_use);
            fprintf(oscilloscope,'DATa:WIDth?');
            dataWidth_raw = fgetl2(oscilloscope);
            dataWidth_check = str2double(dataWidth_raw);
        end
    else
        fprintf(oscilloscope,dataWidth_use);
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('%d byte (%.2f %s)\n',dataWidth,endTime,unit);

%% Oscilloscope Display
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    fieldsList = File.Oscilloscope(deviceNum).Fields;
    isTPS = contains2(model,'tps');
    if numOfDevices > 1
        model = File.Oscilloscope(deviceNum).Model;
        fprintf('Setting view for %s...\n',model);
    end

    % Display
    numOfChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    hasMathChannel = contains2(channelSelect_cell,'MATH');
    if hasMathChannel
        channelSelect_cell{numOfChannels+1} = 'CH4';
        idx_add = 1;
    else
        idx_add = 0;
    end

    % for channel_idx = 1:4
    %     scopeChannel = sprintf('CH%d',channel_idx);
    %     fprintf('\t\tResetting %s for display...',scopeChannel);
    %     startTime = tic;
    %     status_use = sprintf('SELect:%s OFF',scopeChannel);
    %     if isTPS
    %         status = '1';
    %         status_check = '';
    %         status_query = sprintf('SELect:%s?',scopeChannel);
    %         while ~contains2(status_check,status)
    %             fprintf(oscilloscope,status_use);
    %             fprintf(oscilloscope,status_query);
    %             status_check = fgetl2(oscilloscope);
    %         end
    %     else
    %         fprintf(oscilloscope,status_use);
    %     end
    %     [endTime,unit] = getEndTime(startTime);
    %     fprintf('OFF (%.2f %s)\n',endTime,unit)
    % end

    for channel_idx = 1:numOfChannels+idx_add
        scopeChannel = channelSelect_cell{channel_idx};
        channelName = fieldsList(channel_idx);
        fprintf('\tSetting %s...\n',scopeChannel);
        isMathChannel = contains(scopeChannel,'MATH');
        % Display
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('\t\tSetting %s for display...',scopeChannel);
        startTime = tic;
        status_use = sprintf('SELect:%s ON',scopeChannel);
        if isTPS
            status = '1';
            status_check = '';
            status_query = sprintf('SELect:%s?',scopeChannel);
            while ~contains2(status_check,status)
                fprintf(oscilloscope,status_use);
                fprintf(oscilloscope,status_query);
                status_check = fgetl2(oscilloscope);
            end
        else
            fprintf(oscilloscope,status_use);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('ON (%.2f %s)\n',endTime,unit)
    
        % Coupling
        if numOfDevices > 1
            fprintf('\t');
        end

        if ~isMathChannel
            fprintf('\t\tSetting %s coupling...',scopeChannel);
            startTime = tic;
            if contains2(channelName,{'volt','curr','ext','trig'})
                coupling = 'AC';
            else
                coupling = 'DC';
            end
            coupling_use = sprintf('%s:COUPling %s',scopeChannel,coupling);
            if isTPS
                coupling_check = '';
                coupling_query = sprintf('%s:COUPling?',scopeChannel);
                while ~contains2(coupling_check,coupling)
                    fprintf(oscilloscope,coupling_use);	% coupling on voltage
                    fprintf(oscilloscope,coupling_query);sR
                    coupling_check = fgetl2(oscilloscope);
                end
            else
                fprintf(oscilloscope,coupling_use);	% coupling on voltage
            end
            [endTime,unit] = getEndTime(startTime);
            fprintf('%s (%.2f %s)\n',coupling,endTime,unit);
        
            % Unit
            if numOfDevices > 1
                fprintf('\t');
            end
            fprintf('\t\tSetting %s unit...',scopeChannel);
            startTime = tic;
            channelUnit = 'V';
            channelUnit_use = sprintf('%s:YUNit "%s"',scopeChannel,channelUnit);
            if isTPS
                channelUnit_check = '';
                channelUnit_query = sprintf('%s:YUNit?',scopeChannel);
                while ~contains2(channelUnit_check,channelUnit)
                    fprintf(oscilloscope,channelUnit_use);
                    fprintf(oscilloscope,channelUnit_query);
                    channelUnit_check = fgetl2(oscilloscope);
                end
            else
                fprintf(oscilloscope,channelUnit_use);
            end
            [endTime,unit] = getEndTime(startTime);
            fprintf('"%s" (%.2f %s)\n',channelUnit,endTime,unit);
        
            % Probe
            if numOfDevices > 1
                fprintf('\t');
            end
            fprintf('\t\tSetting %s probe...',scopeChannel);
            startTime = tic;
            probe = 1;
            switch channelUnit
                case 'A'
                    probeType = 'CURRENTPROBe';
                case 'V'
                    probeType = 'PRObe';
            end
            probe_use = sprintf('%s:%s 1',scopeChannel,probeType);
            if isTPS
                probe_check = 0;
                probe_query = sprintf('%s:%s?',scopeChannel,probeType);
                while ~isequal(probe_check,probe)
                    fprintf(oscilloscope,probe_use);
                    fprintf(oscilloscope,probe_query);
                    probe_raw = fgetl2(oscilloscope);
                    probe_check = str2double(probe_raw);
                end
            else
                fprintf(oscilloscope,probe_use);
            end
            [endTime,unit] = getEndTime(startTime);
            fprintf('%dX (%.2f %s)\n',probe,endTime,unit);
        
            % Polarity
            if numOfDevices > 1
                fprintf('\t');
            end
            fprintf('\t\tSetting %s polarity...',scopeChannel);
            startTime = tic;
            invert = 'OFF';
            invert_use = sprintf('%s:INVert OFF',scopeChannel);
            if isTPS
                invert_check = '';
                invert_query = sprintf('%s:INVert?',scopeChannel);
                while ~contains2(invert_check,invert)
                    fprintf(oscilloscope,invert_use);
                    fprintf(oscilloscope,invert_query);
                    invert_check = fgetl2(oscilloscope);
                end
            else
                fprintf(oscilloscope,invert_use);
            end
            [endTime,unit] = getEndTime(startTime);
            fprintf('%s (%.2f %s)\n',invert,endTime,unit);
        end

        % Position
        if numOfDevices > 1
            fprintf('\t');
        end
        fprintf('\t\tSetting %s position...',scopeChannel);
        startTime = tic;
        position = 0;
        if isMathChannel
            position_use = sprintf('%s:VERtical:POSition %.2e',scopeChannel,position);
        else
            position_use = sprintf('%s:POSition %.2e',scopeChannel,position);
        end
        if isTPS
            position_check = [];
            if isMathChannel
                position_query = sprintf('%s:VERtical:POSition?',scopeChannel);
            else
                position_query = sprintf('%s:POSition?',scopeChannel);
            end
            while ~isequal(position_check,position)
                fprintf(oscilloscope,position_use);
                fprintf(oscilloscope,position_query);
                position_raw = fgetl2(oscilloscope);
                position_check = str2double(position_raw);
            end
        else
            fprintf(oscilloscope,position_use);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%.2e %s (%.2f %s)\n',position,channelUnit,endTime,unit);
    end

    if hasMathChannel
        fprintf('\tDefining MATH channel...');
        startTime = tic;
        define = 'CH3+CH4';
        define_use = 'MATH:DEFINE "CH3+CH4"';
        if isTPS
            define_check = [];
            define_query = 'MATH:DEFINE?';
            while ~contains2(define_check,define)
                fprintf(oscilloscope,define_use);
                fprintf(oscilloscope,define_query);
                define_raw = fgetl2(oscilloscope);
                define_check = erase(define_raw,{'"',' '});
            end
        else
            fprintf(oscilloscope,define_use);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('OFF (%.2f %s)\n',endTime,unit)

        fprintf('\t\tSetting CH4 for display...');
        startTime = tic;
        status = '1';
        status_use = 'SELect:CH4 OFF';
        if isTPS
            status_check = '';
            status_query ='SELect:CH4?';
            while ~contains2(status_check,status)
                fprintf(oscilloscope,status_use);
                fprintf(oscilloscope,status_query);
                status_check = fgetl2(oscilloscope);
            end
        else
            fprintf(oscilloscope,status_use);
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('OFF (%.2f %s)\n',endTime,unit)
    end
end

%% Trigger
[File,isQuit] = setOscilloscopeTrigger(File,'edge');

%% Settings
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    instr = instrhwinfo(oscilloscope);
    File.Oscilloscope(deviceNum).Info = instr;
    File = getSettings(File,deviceNum,'all');
end

end