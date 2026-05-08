function [File,quitProgram] = setOscilloscope_Tek(File)
instrreset;
%% Variables
confirmResouceSelect = false;
quitProgram = false;

% Structure
Trigger = struct( ...
    'Source','', ...
    'Monitor','');
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
    'VerticalUnit','V');
Oscilloscope = struct( ...
    'Make','', ...
    'Model','', ...
    'Serial','', ...
    'Firmware','', ...
    'Resource','', ...
    'Info',[], ...
    'Trigger',Trigger, ...
    'Settings',Settings, ...
    'Object',[]);
make = '';
model = '';
serial = '';
firmwareVersion = '';

%% Function
% Searching for resources
while confirmResouceSelect == 0
    fprintf('Searching for resources...');
    startTime = tic;
    %     resourceList = visadevlist;
    %     numOfResources = height(resourceList);
    try
        resource = instrhwinfo('visa','tek'); %#ok<INSTHWV> % search for USB resource
        type = 'tek';
    catch
        resource = instrhwinfo('visa','ni'); %#ok<INSTHWV> % search for USB resource
        type = 'ni';
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('found (%.2f %s)\n',endTime,unit);                           % USB resource found
    % device list
    %     resourceModelList = resourceList.Model;
    %     resourceNameList = resourceList.ResourceName;
    resourceName_cell = resource.ObjectConstructorName;  % resource name (cell)
    usb_tf = contains(resourceName_cell,'USB','IgnoreCase',true);
    usb_idx = find(usb_tf);
    isUSB = any(usb_idx);
    strPattern = {'visa(',type,', ',');',''''};
    resourceName_list = erase(resourceName_cell,strPattern);
    empty_idx = cellfun(@isempty,resourceName_list); % identify the empty cells
    resourceName_list(empty_idx) = [];

    % Dialog box
    fprintf('Selecting resource...');
    startTime = tic;
    %     titleListResource = 'Resource Selection';	% list title
    %     promptListCh = {...                 % list prompts
    %         'Select resource to connect.', ...             % insrtuction
    %         'Check with TekVISA and MATLAB Instrument Control for correct resource.'};
    %     [scopeSelect,scopeSelect_tf] = listdlg( ...	% list dialog
    %         'PromptString',promptListCh, ... % list prompts
    %         'ListString',resourceName_list, ...    % list
    %         'SelectionMode','single', ...
    %         'Name',titleListResource);           % list title
    %
    %     % Collect input
    %     if scopeSelect_tf == 0             % cancel detected
    %         fprintf('\nQuitting...\n\n'); % quitting
    %         quitProgram = 1;
    %         return;                     % exit program
    %     end
    %
    %     % Confirm resource selection
    %     resourceName = char(resourceName_list(scopeSelect));
    %     titleQuestResource = 'Confirm Resource Selection';
    %     % Format questions
    %     promptQuestResouceSelect = sprintf('Resource selected:{\\bf%s}',resourceName);
    %     % Question box
    %     questResouceSelect = questdlg( ...               % question dialog
    %         promptQuestResouceSelect, ...                % question prompts
    %         titleQuestResource, ...                    	% question title
    %         BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ... % buttons
    %         opts);                                      % dialog options
    %     % Confirmation
    %     switch questResouceSelect               % apply choice
    %         case BUTTON_CONFIRM                 % check confirmation
    %             confirmResouceSelect = 1;       % confirm info
    %             fprintf('%s\n',resourceName);   % info confirmed
    %         case BUTTON_TRY                     % try again
    %             confirmResouceSelect = 0;       % trying again
    %             fprintf('\nTrying agin...\n\n');% starting over
    %         case BUTTON_CANCEL                  % quit
    %             fprintf('\nQuitting...\n\n');   % quitting
    %             quitProgram = 1;
    %             return;                         % exit program
    %         otherwise                           % cancel
    %             fprintf('\nQuitting...\n\n');   % quitting
    %             quitProgram = 1;
    %             return;                         % exit program
    %     end
    if isUSB
        baudRate = 9600;
        resourceName = resourceName_list{usb_idx};
        terminator = 'LF';
        oscilloscope = visa(type,resourceName);
    else
        baudRate = 19200;
        numOfResources = length(resourceName_list);
        resourceNum_arr = flip(1:numOfResources);
        for resourceNum = resourceNum_arr
            resourceName = resourceName_list{resourceNum};
            oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
            try
                fopen(oscilloscope);
            catch
            end
            status = oscilloscope.Status;
            if contains2(status,'open')
                fclose(oscilloscope);
                break;
            end
        end
        terminator = 'LF';
    end
    File.Oscilloscope.Resource = resourceName;  % resource name
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',resourceName,endTime,unit);   % info confirmed
    confirmResouceSelect = 1;       % confirm info
end

% Connecting to oscilloscope
fprintf('Connecting to oscilloscope...');	% connect to oscilloscope
startTime = tic;
% if isUSB
bufferSize = 20e3;
% bufferSize_use = addCommas(bufferSize);
oscilloscope.InputBufferSize = bufferSize;         % expand data buffer for detailed waveform
ENDIAN_LITTLE = 'littleEndian';
ENDIAN_BIG = 'bigEndian';
byteOrder = ENDIAN_BIG;
oscilloscope.ByteOrder = byteOrder;
timeout = 60;
oscilloscope.Timeout = timeout;
% fprintf('buffer size = %s bytes...',bufferSize_use);
if ~isUSB
    oscilloscope.BaudRate = baudRate;
    oscilloscope.FlowControl = 'hardware';
    oscilloscope.Terminator = terminator;
end
fopen(oscilloscope);                           % start oscillocope
% else
%     resource = instrfind('Type','gpib');
%     boardIdx = resource.BoardIndex;
%     primaryAddress = resource.PrimaryAddress;
%     oscilloscope = gpib('ni',boardIdx,primaryAddress);
%     resourceName = oscilloscope.Name;

%     oscilloscope = serialport(resourceName,baudRate);
%     configureTerminator(oscilloscope,'CR/LF');
%     oscilloscope.FlowControl = 'hardware';
%     oscilloscope.Timeout = 300;
%     scopeID = writeread(oscilloscope,'*IDN?');
% end
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);                           % selected resource

fprintf('Getting information...');
startTime = tic;
id_char = {};
while isempty(id_char)
    fprintf(oscilloscope,'*IDN?');
    pause(1);
    id_char = fgetl(oscilloscope);
    if ~isempty(id_char)
        id_cell = strsplit(id_char,',');
        numOfID = length(id_cell);
        make = id_cell{1};
        File.Oscilloscope.Make = make;
        model = id_cell{2};
        File.Oscilloscope.Model = model;
        switch numOfID
            case 4
                serial = id_cell{3};
                File.Oscilloscope.Serial = serial;
                firmware = strsplit(id_cell{4});
                firmwareVersion = firmware{2};
            case 3
                firmware = strsplit(id_cell{3});
                firmwareVersion = firmware{2};
        end
        File.Oscilloscope.Firmware = firmwareVersion;
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s %s (%.2f %s)\n',make,model,endTime,unit);
    else
        pause(5);
    end
end

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

[File,quitProgram] = getChannels_Tek(File);
if quitProgram
    fprintf('\nQuitting...\n\n'); % quitting
    return;                     % exit program
end

isExt = true;

% Set up acquisition mode
fprintf('Setting acquisition mode...');
startTime = tic;
%  phaseWidth = File.Parameters.PhaseWidth1;
% if phaseWidth >= 100
fprintf(oscilloscope,'ACQuire:MODe AVErage');  % set waveform acquisition to averages
[endTime,unit] = getEndTime(startTime);
fprintf('AVERAGE (%.2f %s)\n',endTime,unit);

% Average Mode
fprintf('Setting average acquisition mode...');
startTime = tic;
numOfSamples = 128;
numOfSamples_use = sprintf('ACQuire:NUMAvg %d',numOfSamples);
fprintf(oscilloscope,numOfSamples_use);    % set number of wavforms for average
[endTime,unit] = getEndTime(startTime);
fprintf('%d samples (%.2f %s)\n',numOfSamples,endTime,unit);  % info confirmed
% else
%      fprintf(oscilloscope,'ACQuire:MODe SAMple');  % set waveform acquisition to samples
%      [endTime,unit] = getEndTime(startTime);
%      fprintf('SAMPLE (%.2f %s)\n',endTime,unit);
% end
fprintf(oscilloscope,'ACQuire:STAte RUN');

% Headers
fprintf('Setting headers...');
startTime = tic;
fprintf(oscilloscope,'HEADer OFF');
[endTime,unit] = getEndTime(startTime);
fprintf('OFF (%.2f %s)\n',endTime,unit);

% Data Encoding
fprintf('Setting data encoding...');
startTime = tic;
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
fprintf(oscilloscope,dataEncoding_use);  % encoding for sensible data output
[endTime,unit] = getEndTime(startTime);
fprintf('%s (%.2f %s)\n',dataEncoding,endTime,unit);

% Binary Order
if contains2(dataEncoding,'BIN')
    fprintf('Setting binary order...');
    startTime = tic;
    fprintf(oscilloscope,'WFMPre:BYT_Or?');
    binOrder_raw = fgetl(oscilloscope);
    binOrder_check = erase(binOrder_raw,newline);
    while ~contains2(binOrder,binOrder_check)
        binOrder_use = sprintf('WFMPre:BYT_Or %s',binOrder);
        fprintf(oscilloscope,binOrder_use);
        fprintf(oscilloscope,'WFMPre:BYT_Or?');
        binOrder_raw = fgetl(oscilloscope);
        binOrder_check = erase(binOrder_raw,newline);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',binOrder_check,endTime,unit);
end

% Data Width
fprintf('Setting data width...');
startTime = tic;
fprintf(oscilloscope,'DATa:ENCdg?');
dataEncoding_raw = fgetl(oscilloscope);
dataEncoding = erase(dataEncoding_raw,newline);
if contains2(dataEncoding,'ASC')
    dataWidth = 1;
else
    %     if isUSB
    %         dataWidth = 1;
    %     else
    dataWidth = 2;
    %     end
end
dataWidth_use = sprintf('DATa:WIDth %d',dataWidth);
fprintf(oscilloscope,dataWidth_use);
% Check
fprintf(oscilloscope,'DATa:WIDth?');
dataWidth_raw = fgetl(oscilloscope);
dataWidth_check = str2double(dataWidth_raw);
while dataWidth ~= dataWidth_check
    fprintf(oscilloscope,dataWidth_use);
    fprintf(oscilloscope,'DATa:WIDth?');
    dataWidth_raw = fgetl(oscilloscope);
    dataWidth_check = str2double(dataWidth_raw);
end
[endTime,unit] = getEndTime(startTime);
fprintf('%d byte (%.2f %s)\n',dataWidth,endTime,unit);

% Display
fprintf('Resetting channels for display...');
startTime = tic;
fprintf(oscilloscope,'SELect:CH1 OFF');
fprintf(oscilloscope,'SELect:CH2 OFF');
fprintf(oscilloscope,'SELect:CH3 OFF');
fprintf(oscilloscope,'SELect:CH4 OFF');
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit)

numOfChannels = File.Oscilloscope.NumberOfChannels;
channelSelect_cell = File.Oscilloscope.Channels;
for channel_idx = 1:numOfChannels
    scopeChannel = channelSelect_cell{channel_idx};
    % Display
    fprintf('Setting %s for display...',scopeChannel);
    startTime = tic;
    status = 'ON';
    status_check = '';
    status_use = sprintf('SELect:%s ON',scopeChannel);
    while ~contains2(status_check,status)
        fprintf(oscilloscope,status_use);
        fprintf(oscilloscope,'SELect?');
        status_raw = fgetl2(oscilloscope);
        status_cell = strsplit(status_raw,';');
        status_channel = status_cell{channel_idx};
        if contains2(status_channel,'1')
            status_check = 'ON';
        else
            status_check = 'OFF';
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',status,endTime,unit)

    % Coupling
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
    while probe_check ~= probe
        fprintf(oscilloscope,probe_use);
        fprintf(oscilloscope,probe_query);
        probe_raw = fgetl2(oscilloscope);
        probe_check = str2double(probe_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%dX (%.2f %s)\n',probe,endTime,unit);

    % Polarity
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
    fprintf('Setting %s position...',scopeChannel);
    startTime = tic;
    position = 0;
    position_check = [];
    position_use = sprintf('%s:POSition %.2e',scopeChannel,position);
    position_query = sprintf('%s:POSition',scopeChannel);
    while position_check ~= position
        fprintf(oscilloscope,position_use);
        fprintf(oscilloscope,position_query);
        position_raw = fgetl2(oscillosocpe);
        position_check = str2double(position_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e %s (%.2f %s)\n',position,channelUnit,endTime,unit);
end

% Trigger setup
fprintf('Setting trigger...');
startTime = tic;
fprintf(oscilloscope,'TRIGger:MODe NORMal');                   % trigger mode on normal
fprintf('normal...');
if isExt
    trigSource = 'EXT';
    slope = 'RISe';
    edge = 'rising';
end
trigSource_use = sprintf('TRIGger:MAIn:EDGe:SOUrce %s',trigSource);
fprintf(oscilloscope,trigSource_use);          % trigger source
fprintf('%s...',trigSource);
fprintf(oscilloscope,'TRIGger:MAIn:EDGE:COUPling DC');         % trigger on DC coupling
fprintf('DC...');
slope_use = sprintf('TRIGger:MAIn:EDGe:SLOpe %s',slope);
fprintf(oscilloscope,slope_use);  % trigger on rising slope
edge_use = sprintf('%s edge',edge);
fprintf(edge_use);
[endTime,unit] = getEndTime(startTime);
fprintf(' (%.2f %s)\n',endTime,unit);

instr = instrhwinfo(oscilloscope);
File.Oscilloscope.Info = instr;
File.Oscilloscope.Trigger.Source = trigSource;
File.Oscilloscope.Trigger.Monitor = '';
File.Oscilloscope.Object = oscilloscope;            % object

% Settings
File = getSettings(File,[]);

end