function File = getSettings(File,scopeChannel)
%% Variables
% Object
isStruct = isstruct(File);
isVISA = strcmpi(class(File),'visa');
if isStruct
    scope = File.Oscilloscope.Object;      % oscilloscope
elseif isVISA
    scope = File;
end
scope = setScopeStatus(scope,'open');

% Structure
Info = struct(...
    'Channel','CH1',...
    'Coupling','DC',...
    'VerticalScale',4.0E-8,...
    'HorizontalScale',0,...
    'DataLength',2500,...
    'AcquisitionMode','Average mode');

Settings = struct(...
    'DataWidth',2,...
    'Bits',16,...
    'DataEncoding','ASC',...
    'BinaryFormat','RP',...
    'BinaryOrder','MSB',...
    'DataLength',2500,...
    'Info',Info,...
    'ReferenceFormat','Y',...
    'Interval',4.0E-8,...
    'TriggerOffset',0,...
    'HorizontalStart',0,...
    'HorizontalUnit','s',...
    'VerticalScaleFactor',0,...
    'WaveformConversion',0,...
    'VerticalPosition',0,...
    'VerticalUnit','V');

%% Function
source_use = sprintf('DATa:SOUrce %s',scopeChannel);
fprintf(scope,source_use);
fprintf(scope,'WFMPre?');                            % query waveform data
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
data = fscanf(scope);
data_fixed = erase(data,'"');
data_formated = strsplit(data_fixed,';')

Settings.DataWidth = str2num(data_formated{1}); %#ok<*ST2NM> 
Settings.Bits = str2num(data_formated{2});
Settings.DataEncoding = data_formated{3};
Settings.BinaryFormat = data_formated{4};
Settings.BinaryOrder = data_formated{5};
Settings.DataLength = str2num(data_formated{6});
Settings.ReferenceFormat = data_formated{8};
Settings.Interval = str2num(data_formated{9});
Settings.TriggerOffset = str2num(data_formated{10});
Settings.HorizontalStart = str2num(data_formated{11});
Settings.HorizontalUnit = data_formated{12};
Settings.VerticalScaleFactor = str2num(data_formated{13});
Settings.WaveformConversion = str2num(data_formated{14});
Settings.VerticalPosition = str2num(data_formated{15});
Settings.VerticalUnit = erase(data_formated{16},newline);

wfid = data_formated{7};
wfid_formatted = strsplit(wfid,', ');
Info.Channel = upper(wfid_formatted{1});
coupling = wfid_formatted{2};
coupling_formatted = strsplit(coupling);
Info.Coupling = coupling_formatted{1};
verticalScale = wfid_formatted{3};
verticalScale_formatted = strsplit(verticalScale);
Info.VerticalScale = str2num(verticalScale_formatted{1});
horizontalScale = wfid_formatted{4};
horizontalScale_formatted = strsplit(horizontalScale);
Info.HorizontalScale = str2num(horizontalScale_formatted{1});
dataLength = wfid_formatted{5};
dataLength_formatted = strsplit(dataLength);
Info.DataLength = str2num(dataLength_formatted{1});
acquisitionMode = wfid_formatted{6};
acquisitionMode_formatted = strsplit(acquisitionMode);
Info.AcquisitionMode = acquisitionMode_formatted{1};
Settings.Info = Info;

File.Oscilloscope.Settings = Settings;

end